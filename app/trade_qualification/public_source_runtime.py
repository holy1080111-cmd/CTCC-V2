"""Stage-bound native public sources; no credential or submission capability.

Legacy collector objects stay replay data. Only this invocation's private source
registry can issue a one-use handoff to the diagnostic coordinator.
"""

from __future__ import annotations

import asyncio
import os
import ssl
from dataclasses import dataclass
from threading import get_ident
from typing import Literal
from weakref import WeakKeyDictionary

import httpcore
import httpx
from websockets.asyncio.client import ClientConnection

import app.public_market_source.public_receipt_storage as _receipt_storage
import app.public_market_source.public_runtime_journal as _runtime_journal
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    PublicReceiptError,
    canonical,
    decode,
    demo_public_v3_time_policy_sha256,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.public_market_source.public_clock import (
    _observe_owned_runtime_clock,
)
from app.public_market_source.public_runtime_journal import (
    _SAFE_CLOCK_FAILURE_CODES,
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
from app.trade_qualification import demo_public_origin_policy_v2 as demo_policy_v2
from app.trade_qualification import market_aux_collector as aux
from app.trade_qualification import public_market_collector as public
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import quote_collector as quotes
from app.trade_qualification import ws_collector as ws

_ISSUER = object()
_SOURCES = WeakKeyDictionary()
_RESULTS = WeakKeyDictionary()
_INITIAL_RESULTS = WeakKeyDictionary()
_V3_TIME_PHASES = WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class _V3TimePhase:
    attempt: object
    pre_request_id: int
    pre_validation_head: str
    market_prefix_head: str | None = None
    market_packet: object | None = None
    market_packet_sha256: str | None = None


def _reopen_runtime_journal(root):
    """Keep native public-journal ownership in this reviewed source facade."""
    return _runtime_journal._root_context(root)


def _native_recheck_root(root):
    """Use the unpatched native receipt root for a separate bounded artifact."""
    return _receipt_storage._root_context(root)


def _native_recheck_root_identity(root):
    return _receipt_storage._root_identity(root)


class PublicSourceRuntimeError(ValueError):
    """Fixed local codes; never a transport exception or response body."""


def _require_trusted_v2_demo_origin_profile(plan):
    """Refuse new Demo capture until an account-bound origin can be enforced.

    A reviewed route and a same-session declaration are policy pins, not
    authenticated registration-region evidence. The dormant V2 transport
    builders are route-bound, but caller-supplied pins cannot authenticate an
    account. Historical V2 journals replay as DENY.
    """
    if type(plan) is not dict:
        raise PublicSourceRuntimeError("trusted_demo_public_origin_profile_unavailable")
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
    if "account_plan_sha256" in plan or "demo_public_origin_policy_sha256" in plan:
        account_pin = plan.get("account_plan_sha256")
        policy_pin = plan.get("demo_public_origin_policy_sha256")
        if (
            type(account_pin) is not str
            or len(account_pin) != 64
            or any(char not in "0123456789abcdef" for char in account_pin)
            or type(policy_pin) is not str
            or policy_pin
            != sha(
                demo_policy_v2.freeze_demo_public_origin_policy_v2(
                    route.registration_region
                )
            )
            or plan.get("registration_region_authenticated") is not False
        ):
            raise PublicSourceRuntimeError(
                "trusted_demo_public_origin_profile_unavailable"
            )
    # The declaration pins a route to this invocation, but does not authenticate
    # the account's registration region. Native IO remains unreachable.
    raise PublicSourceRuntimeError("trusted_demo_public_origin_profile_unavailable")


def _planned_public_route(plan):
    """Read exact reviewed route policy; never authenticate its account claim."""
    if (
        type(plan) is not dict
        or plan.get("schema_version")
        not in {
            "ctcc.public.runtime_plan.v2",
            "ctcc.public.initial_runtime_plan.v2",
            "ctcc.public.runtime_plan.v3",
            "ctcc.public.initial_runtime_plan.v3",
            "ctcc.public.initial_runtime_plan.v4",
        }
        or "registration_region" not in plan
    ):
        return None
    try:
        route = demo_origin.reviewed_demo_public_route(plan["registration_region"])
    except demo_origin.DemoPublicOriginError:
        raise PublicSourceRuntimeError("demo_public_origin_mismatch") from None
    if (
        plan.get("environment") != "demo"
        or plan.get("rest_origin") != route.rest_origin
        or plan.get("ws_origin") != route.ws_origin
    ):
        raise PublicSourceRuntimeError("demo_public_origin_mismatch")
    return route


def _owned_public_route(source):
    """Resolve a collector route only after the unchanged native IO fence."""
    state = _state(source)
    _fence_v2_demo_transport(state)
    return _planned_public_route(state["plan"])


def _owned_ws_endpoint(source):
    route = _owned_public_route(source)
    return (
        ws.PUBLIC_WS_URL
        if route is None
        else demo_origin.build_demo_public_ws_endpoint(route)
    )


def _fence_v2_demo_transport(state):
    """Recheck the still-denied V2 origin before any native public transport.

    V1 remains pinned to Production. The dormant V2 builders can construct only
    the reviewed route, but no account registration-region proof exists yet.
    Checking every IO entry prevents a skipped capture-level check from
    sending a Demo-labelled request to any public service.
    """
    plan = state.get("plan")
    if type(plan) is dict:
        if plan.get("schema_version") == "ctcc.public.initial_runtime_plan.v4":
            _require_owned_v4_route(state)
            return
        if plan.get("schema_version") in {
            "ctcc.public.runtime_plan.v2",
            "ctcc.public.initial_runtime_plan.v2",
            "ctcc.public.runtime_plan.v3",
            "ctcc.public.initial_runtime_plan.v3",
            "ctcc.public.initial_runtime_plan.v4",
        } or any(
            key in plan
            for key in (
                "registration_region",
                "account_plan_sha256",
                "demo_public_origin_policy_sha256",
            )
        ):
            _require_trusted_v2_demo_origin_profile(plan)
        if plan.get("schema_version") in {
            "ctcc.public.runtime_plan.v1",
            "ctcc.public.initial_runtime_plan.v1",
        } and (
            plan.get("rest_origin") != quotes.BASE_URL
            or plan.get("ws_origin") not in {ws.PUBLIC_WS_URL, ws.LEGACY_PUBLIC_WS_URL}
        ):
            raise PublicSourceRuntimeError("public_legacy_origin_mismatch")


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


class _CapturedPublicV3(_CapturedPublic):
    """Exact v3 post-publication handoff; v2 consumers cannot replay it."""

    __slots__ = ()


class _CapturedInitialPublicV3(_CapturedPublic):
    """Exact v3 initial handoff; only the issuing task may consume it."""

    __slots__ = ()


class _CapturedInitialPublicV4(_CapturedPublic):
    __slots__ = ()


def _require_owned_v4_route(state):
    from app.trade_qualification import demo_operational_route_v4 as operational

    try:
        claim = operational._owned_route_state_v4(
            state.get("v4_route_claim"),
            invocation=state.get("invocation"),
            owner_task=state.get("parent"),
        )
        plan = state["plan"]
        if (
            plan["schema_version"] != "ctcc.public.initial_runtime_plan.v4"
            or plan["operational_route_proof_sha256"] != state["v4_route_proof_sha256"]
            or plan["account_plan_sha256"] != claim["plan_sha256"]
            or plan["rest_origin"] != claim["route"].rest_origin
            or plan["ws_origin"] != claim["route"].ws_origin
            or plan["registration_site"] != "UNKNOWN"
            or plan["registration_region_authenticated"] is not False
        ):
            raise ValueError
        return claim
    except Exception:  # noqa: BLE001 -- never expose private route or account data
        raise PublicSourceRuntimeError("public_v4_owned_route_required") from None


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
    _fence_v2_demo_transport(state)
    v3 = state["plan"].get("schema_version") in {
        "ctcc.public.initial_runtime_plan.v3",
        "ctcc.public.initial_runtime_plan.v4",
        "ctcc.public.runtime_plan.v3",
    }
    v3_time = v3 and role in {"time_pre", "time_post"}
    if (
        not (role in ("quote", "candles", "market_aux") or v3_time)
        or role in state["client_roles"]
    ):
        raise PublicSourceRuntimeError("public_source_client_scope_invalid")
    if v3:
        if role == "time_pre":
            if (
                _V3_TIME_PHASES.get(source) is not None
                or state["clients"]
                or state.get("responses")
                or state.get("ws_claimed")
            ):
                raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
        elif role == "time_post":
            _require_v3_post_time_phase(source)
            # Burn the one-shot post-time capability before client construction.
            _V3_TIME_PHASES.pop(source, None)
        else:
            _require_v3_pre_market_phase(source)
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
    route = _planned_public_route(_state(source)["plan"])
    hostname = "www.okx.com" if route is None else route.rest_hostname
    return _tls_proof(stream.get_extra_info("ssl_object"), context, hostname)


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
    _fence_v2_demo_transport(state)
    role, _ = _verified_client(source, client)
    route = _planned_public_route(state["plan"])
    time_role = role in {"time_pre", "time_post"}
    if (
        state["plan"].get("schema_version")
        in {
            "ctcc.public.initial_runtime_plan.v3",
            "ctcc.public.initial_runtime_plan.v4",
            "ctcc.public.runtime_plan.v3",
        }
        and not time_role
    ):
        _require_v3_pre_market_phase(source)
    if time_role and state["plan"].get("schema_version") not in {
        "ctcc.public.initial_runtime_plan.v3",
        "ctcc.public.initial_runtime_plan.v4",
        "ctcc.public.runtime_plan.v3",
    }:
        raise PublicSourceRuntimeError("public_request_scope_invalid")
    if (
        type(request) is not httpx.Request
        or request.method != "GET"
        or request.url.scheme != "https"
        or request.url.host != ("www.okx.com" if route is None else route.rest_hostname)
        or request.url.port not in (None, 443)
        or request.content
    ):
        raise PublicSourceRuntimeError("public_request_scope_invalid")
    if route is not None:
        try:
            if time_role:
                demo_origin.validate_demo_public_time_request_v3(route, role, request)
            else:
                demo_origin.validate_demo_public_request(
                    route, role, request, expected_instrument_id=state["instrument_id"]
                )
        except demo_origin.DemoPublicOriginError:
            raise PublicSourceRuntimeError("public_request_scope_invalid") from None
    allowed_paths = {
        "quote": {p for _, p in quotes.ENDPOINTS},
        "candles": {candles.ENDPOINT},
        "market_aux": {p for _, p in aux.ENDPOINTS},
    }
    if time_role:
        allowed_paths[role] = {"/api/v5/public/time"}
    pairs = tuple(request.url.params.multi_items())
    query = dict(pairs)
    if (
        request.url.path not in allowed_paths[role]
        or len(query) != len(pairs)
        or (bool(query) if time_role else query.get("instId") != state["instrument_id"])
        or started != utc_from_ns(state["last"]["utc_ns"])
    ):
        raise PublicSourceRuntimeError("public_request_scope_invalid")
    expected = {} if time_role else {"instId": state["instrument_id"]}
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
    if route is None and (
        set(request.headers) - {"host", "accept", "accept-encoding", "user-agent"}
        or request.headers.get("accept-encoding") != "identity"
    ):
        raise PublicSourceRuntimeError("public_request_headers_invalid")
    index = state["request_count"]
    if index >= 64:
        raise PublicSourceRuntimeError("public_request_limit")
    state["request_count"] += 1
    metadata = {
        "id": index,
        "role": role,
        "method": "GET",
        "origin": quotes.BASE_URL if route is None else route.rest_origin,
        "endpoint": request.url.path,
        "query": pairs,
        "started": state["last"],
    }
    if route is not None:
        # These are fixed public headers only; no credential or cookie can enter
        # the journal because the exact Demo request was checked above.
        metadata["request_headers"] = tuple(
            sorted((name.lower(), value) for name, value in request.headers.items())
        )
    _event(source, "request", metadata)
    if time_role:
        state.setdefault("time_started", {})[role] = state["last"]
        state.setdefault("time_request_ids", {})[role] = index
    return index


async def _fetch_http(source, client, request, started, policy):
    """Retain actual bytes before parser rejection, without changing old DTOs."""
    index = _request_scope(source, client, request, started)
    state = _state(source)
    route = _planned_public_route(state["plan"])
    role = state["clients"][client][0]
    body, response, cancelled = bytearray(), None, False
    rejection = None
    try:
        async with asyncio.timeout(policy.request_timeout_seconds):
            if (
                state["plan"].get("schema_version")
                == "ctcc.public.initial_runtime_plan.v4"
            ):
                _require_owned_v4_route(state)
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
                    "hostname": "www.okx.com" if route is None else route.rest_hostname,
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
            if role in {"time_pre", "time_post"} and receipt_stamp is not None:
                state.setdefault("time_headers", {})[role] = receipt_stamp
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
            if route is not None:
                try:
                    if role in {"time_pre", "time_post"}:
                        demo_origin.validate_demo_public_time_response_v3(
                            route, role, request, response
                        )
                    else:
                        demo_origin.validate_demo_public_response(
                            route,
                            role,
                            request,
                            response,
                            expected_instrument_id=state["instrument_id"],
                        )
                except demo_origin.DemoPublicOriginError:
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
            if role in {"time_pre", "time_post"}:
                state.setdefault("time_body_complete", {})[role] = state["last"]
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
    state = _state(source)
    record = (
        request.url.path,
        tuple(request.url.params.multi_items()),
        bytes(body),
        started,
        received,
        completed,
    )
    if role in {"time_pre", "time_post"}:
        state.setdefault("time_http", {})[role] = {
            "raw": bytes(body),
            "request_start": state["time_started"][role],
            "headers_received": state["time_headers"][role],
            "body_complete": state["time_body_complete"][role],
            "response_closed": state["last"],
        }
        state.setdefault("time_responses", []).append(record)
    else:
        state["responses"].append(record)
    return bytes(body), received, completed


def _v3_time_prefix(source, phase, *, market_packet=None):
    """Bind a private phase to this attempt and its original raw validation."""
    state = _state(source)
    if (
        type(phase) is not _V3TimePhase
        or phase.attempt is not state.get("attempt")
        or state.get("time_request_ids", {}).get("time_pre") != phase.pre_request_id
        or set(state.get("time_http", {})) != {"time_pre"}
    ):
        raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
    head, events = _runtime_journal._replay_open_v3_prefix(
        phase.attempt, market_packet=market_packet
    )
    validated = [
        event
        for event in events
        if event["kind"] == "time_validated"
        and event["metadata"].get("id") == phase.pre_request_id
    ]
    if (
        len(validated) != 1
        or sha(canonical(validated[0])) != phase.pre_validation_head
        or any(
            event["kind"] == "request" and event["metadata"].get("role") == "time_post"
            for event in events
        )
    ):
        raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
    return head, events


def _complete_owned_public_market_v3(source, packet, *, _owner):
    """Owner-only market completion; callers cannot set a mutable phase flag."""
    if _owner is not _ISSUER:
        raise PublicSourceRuntimeError("public_v3_market_owner_required")
    state = _state(source)
    _fence_v2_demo_transport(state)
    phase = _V3_TIME_PHASES.get(source)
    if phase is None or phase.market_prefix_head is not None:
        raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
    canonical_packet = public_v2._parts(packet)[0]
    _check_packet_binding_v2(
        state,
        canonical_packet,
        expected_roles=("quote", "candles", "market_aux", "time_pre"),
    )
    head, events = _v3_time_prefix(
        source, phase, market_packet=canonical_packet.packet_json
    )
    if len(
        [event for event in events if event["kind"].startswith("ws_")]
    ) != 7 or not any(
        event["kind"] == "request" and event["metadata"].get("role") == "candles"
        for event in events
    ):
        raise PublicSourceRuntimeError("public_v3_market_phase_incomplete")
    _V3_TIME_PHASES[source] = _V3TimePhase(
        attempt=phase.attempt,
        pre_request_id=phase.pre_request_id,
        pre_validation_head=phase.pre_validation_head,
        market_prefix_head=head,
        market_packet=canonical_packet,
        market_packet_sha256=sha(canonical_packet.packet_json),
    )
    return canonical_packet


def _require_v3_pre_market_phase(source):
    phase = _V3_TIME_PHASES.get(source)
    if phase is None or phase.market_prefix_head is not None:
        raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
    _v3_time_prefix(source, phase)


def _require_v3_post_time_phase(source):
    state = _state(source)
    phase = _V3_TIME_PHASES.get(source)
    if (
        phase is None
        or phase.market_prefix_head is None
        or phase.market_packet is None
        or sha(phase.market_packet.packet_json) != phase.market_packet_sha256
    ):
        raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
    head, _ = _v3_time_prefix(
        source, phase, market_packet=phase.market_packet.packet_json
    )
    if head != phase.market_prefix_head:
        raise PublicSourceRuntimeError("public_v3_time_phase_changed")
    _check_packet_binding_v2(
        state,
        phase.market_packet,
        expected_roles=("quote", "candles", "market_aux", "time_pre"),
    )
    return phase


async def _fetch_owned_public_time_v3(source, role):
    """One private time role; a V3 issuer must still supply trusted origin proof."""
    state = _state(source)
    _fence_v2_demo_transport(state)
    if (
        state["plan"].get("schema_version")
        not in {
            "ctcc.public.initial_runtime_plan.v3",
            "ctcc.public.runtime_plan.v3",
            "ctcc.public.initial_runtime_plan.v4",
        }
        or role not in {"time_pre", "time_post"}
        or role in state.get("time_http", {})
        or role in state.get("client_roles", set())
    ):
        raise PublicSourceRuntimeError("public_v3_time_role_invalid")
    if role == "time_pre":
        if (
            _V3_TIME_PHASES.get(source) is not None
            or state.get("clients")
            or state.get("responses")
            or state.get("ws_records")
            or state.get("ws_claimed")
        ):
            raise PublicSourceRuntimeError("public_v3_time_phase_invalid")
    else:
        _require_v3_post_time_phase(source)
    route = _planned_public_route(state["plan"])
    if route is None:
        raise PublicSourceRuntimeError("public_v3_time_route_invalid")
    client = _new_owned_client(source, role)
    cancelled = False
    validation_head = None
    try:
        started = _sample(source)
        request = demo_origin.build_demo_public_time_request_v3(route, role)
        raw, _, _ = await _fetch_http(
            source,
            client,
            request,
            started,
            _PublicTimePolicyV3(),
        )
        observation = _state(source)["time_http"][role]
        server_ms, canonical_sha256 = _runtime_journal._public_system_time(raw)
        _sample(source)
        validated = _state(source)["last"]
        if (
            not observation["request_start"]["utc_ns"]
            <= int(server_ms) * 1_000_000
            <= observation["headers_received"]["utc_ns"]
            or validated["monotonic_ns"] - observation["request_start"]["monotonic_ns"]
            > 5_000_000_000
        ):
            raise PublicSourceRuntimeError("public_v3_time_interval_invalid")
        validation_head = _event(
            source,
            "time_validated",
            {
                "id": _state(source)["time_request_ids"][role],
                "validated": validated,
                "server_time_ms": server_ms,
                "canonical_body_sha256": canonical_sha256,
            },
        )
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        await public._close_client(client, 1, pending_cancel=cancelled)
    if role == "time_pre":
        phase = _V3TimePhase(
            attempt=state["attempt"],
            pre_request_id=state["time_request_ids"]["time_pre"],
            pre_validation_head=validation_head,
        )
        _v3_time_prefix(source, phase)
        _V3_TIME_PHASES[source] = phase
    return server_ms


@dataclass(frozen=True, slots=True)
class _PublicTimePolicyV3:
    request_timeout_seconds: Literal[2] = 2
    max_response_bytes: Literal[4096] = 4096


def _ws_options(source, started):
    state = _state(source)
    _fence_v2_demo_transport(state)
    if state["plan"].get("schema_version") in {
        "ctcc.public.initial_runtime_plan.v3",
        "ctcc.public.initial_runtime_plan.v4",
        "ctcc.public.runtime_plan.v3",
    }:
        _require_v3_pre_market_phase(source)
    route = _planned_public_route(state["plan"])
    endpoint = (
        ws.PUBLIC_WS_URL
        if route is None
        else demo_origin.build_demo_public_ws_endpoint(route)
    )
    if state["ws_claimed"] or started != utc_from_ns(state["last"]["utc_ns"]):
        raise PublicSourceRuntimeError("public_socket_already_claimed")
    state["ws_claimed"] = True
    state["ws_context"] = ssl.create_default_context()
    _event(source, "ws_connect", {"endpoint": endpoint, "started": state["last"]})
    return {"ssl": state["ws_context"]}


def _ws_connected(source, socket):
    state = _state(source)
    if state["plan"].get("schema_version") == "ctcc.public.initial_runtime_plan.v4":
        _require_owned_v4_route(state)
    if type(socket) is not ClientConnection or state["socket"] is not None:
        raise PublicSourceRuntimeError("owned_public_socket_required")
    context = state["ws_context"]
    if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
        raise PublicSourceRuntimeError("verified_public_tls_required")
    route = _planned_public_route(state["plan"])
    hostname = "ws.okx.com" if route is None else route.ws_hostname
    proof = _tls_proof(socket.transport.get_extra_info("ssl_object"), context, hostname)
    state["socket"] = socket
    _event(source, "ws_connected", {"connected": state["last"], "tls": proof})


def _ws_record(source, socket, kind, raw=None):
    state = _state(source)
    if state["plan"].get("schema_version") == "ctcc.public.initial_runtime_plan.v4":
        _require_owned_v4_route(state)
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
    if state["plan"].get("schema_version") == "ctcc.public.initial_runtime_plan.v4":
        _require_owned_v4_route(state)
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


def _check_packet_binding_v2(
    state, packet, *, expected_roles=("quote", "candles", "market_aux")
):
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
        expected_roles=expected_roles,
    )


def _check_raw_inventory(
    state, observations, reference, *, expected_roles=("quote", "candles", "market_aux")
):
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
        or sorted(role for role, _ in state["clients"].values())
        != sorted(expected_roles)
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


async def _capture_after_publication_v3(publication, policy, root):
    """Owned V3 time bracket; the Demo origin fence still denies native IO."""
    return await _capture_owned(
        publication, policy, root, stage="post_publication", _version=3
    )


async def _capture_initial_public_v3(initial_scope, policy, root):
    """Owned V3 time bracket; only a private initial scope can enter."""
    return await _capture_owned(
        initial_scope, policy, root, stage="initial_public", _version=3
    )


async def _capture_initial_public_v4(initial_scope, policy, root):
    return await _capture_owned(
        initial_scope, policy, root, stage="initial_public", _version=4
    )


async def _capture_owned(scope, policy, root, *, stage, _version=1):
    # The common transport never accepts a caller plan/DTO as ownership. Each
    # stage consumes its own exact private issuer before any clock or IO.
    if (
        type(stage) is not str
        or type(_version) is not int
        or _version not in (1, 2, 3, 4)
    ):
        raise PublicSourceRuntimeError("public_capture_stage_invalid")
    full_public = _version in (2, 3, 4)
    if _version == 4 and stage != "initial_public":
        raise PublicSourceRuntimeError("public_v4_initial_stage_required")
    if stage == "post_publication":
        from app.trade_qualification.post_g12_public_runtime import (
            _take_publication,
            _take_publication_v2,
        )

        claim = (_take_publication_v2 if full_public else _take_publication)(scope)
        version, boundary = f"ctcc.public.runtime_plan.v{_version}", claim["barrier"]
        carrier_type, results = (
            {
                1: _CapturedPublic,
                2: _CapturedPublicV2,
                3: _CapturedPublicV3,
            }[_version],
            _RESULTS,
        )
    elif stage == "initial_public":
        from app.trade_qualification.qualification_runtime import (
            _take_initial_scope,
            _take_initial_scope_v2,
        )

        if _version == 4:
            from app.trade_qualification.demo_market_source_v4 import (
                _take_initial_scope_v4,
            )

            claim = _take_initial_scope_v4(scope)
        else:
            claim = (_take_initial_scope_v2 if full_public else _take_initial_scope)(
                scope
            )
        version, boundary = (
            f"ctcc.public.initial_runtime_plan.v{_version}",
            claim["invocation_started"],
        )
        carrier_type, results = (
            {
                1: _CapturedInitialPublic,
                2: _CapturedInitialPublicV2,
                3: _CapturedInitialPublicV3,
                4: _CapturedInitialPublicV4,
            }[_version],
            _INITIAL_RESULTS,
        )
    else:
        raise PublicSourceRuntimeError("public_capture_stage_invalid")
    selected = (
        public_v2._policy_copy(policy) if full_public else public._policy_copy(policy)
    )
    route = (
        demo_origin.reviewed_demo_public_route(claim["plan"]["registration_region"])
        if full_public and "registration_region" in claim["plan"]
        else None
    )
    policy_digest = (
        public_v2._policy_digest(selected, route)
        if full_public
        else public._digest(selected)
    )
    if (stage == "initial_public" or full_public) and claim["plan"][
        "policy_sha256"
    ] != policy_digest:
        raise PublicSourceRuntimeError("public_initial_policy_changed")
    plan = {
        "schema_version": version,
        **claim["plan"],
        "policy_sha256": policy_digest,
    }
    if _version in (3, 4):
        if route is None:
            raise PublicSourceRuntimeError(
                "trusted_demo_public_origin_profile_unavailable"
            )
        plan["exchange_time_policy_sha256"] = demo_public_v3_time_policy_sha256(
            route.registration_region
        )
    if full_public and (
        plan.get("stage") != stage
        or any(
            plan.get(key) != value for key, value in public_v2._plan_pins(route).items()
        )
    ):
        raise PublicSourceRuntimeError("public_v2_owned_plan_required")
    if full_public and _version != 4:
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
    if full_public:
        state["plan"] = plan
    state["deadline"] = state["loop"].time() + selected.total_timeout_seconds
    state["clock"] = lambda: _sample(source)
    _SOURCES[source] = state
    packet = None
    try:
        if _version == 4:
            _require_owned_v4_route(state)
        with _runtime_attempt(root, plan) as attempt:
            state["attempt"] = attempt
            try:
                result = _record_clock(
                    attempt, "before", _observe_owned_runtime_clock(attempt, "before")
                )
                if result["outcome"] != "accepted":
                    raise PublicSourceRuntimeError("native_public_clock_rejected")
                _accept_clock_sample(state, result)
                if _version in (3, 4):
                    await _fetch_owned_public_time_v3(source, "time_pre")
                    packet = await (
                        public_v2._collect_owned_public_market_v4(source)
                        if _version == 4
                        else public_v2._collect_owned_public_market_v2(source)
                    )
                    packet = _complete_owned_public_market_v3(
                        source, packet, _owner=_ISSUER
                    )
                    await _fetch_owned_public_time_v3(source, "time_post")
                elif _version == 2:
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
                if full_public:
                    packet = public_v2._parts(packet)[0]
                    _check_packet_binding_v2(
                        state,
                        packet,
                        expected_roles=(
                            "quote",
                            "candles",
                            "market_aux",
                            "time_pre",
                            "time_post",
                        )
                        if _version in (3, 4)
                        else ("quote", "candles", "market_aux"),
                    )
                else:
                    packet = public.validate_collected_public_market(packet)
                    _check_packet_binding(state, packet)
                _sample(source)
                encoded = (
                    packet.packet_json
                    if full_public
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
        _V3_TIME_PHASES.pop(source, None)


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


def _consume_public_capture_v3(value, invocation):
    return _consume_capture(value, invocation, _CapturedPublicV3, _RESULTS)


def _consume_initial_public_capture_v3(value, invocation):
    return _consume_capture(
        value, invocation, _CapturedInitialPublicV3, _INITIAL_RESULTS
    )


def _consume_initial_public_capture_v4(value, invocation):
    return _consume_capture(
        value, invocation, _CapturedInitialPublicV4, _INITIAL_RESULTS
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
        if expected
        in (
            _CapturedPublicV2,
            _CapturedInitialPublicV2,
            _CapturedPublicV3,
            _CapturedInitialPublicV3,
            _CapturedInitialPublicV4,
        )
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
        "ctcc.public.initial_runtime_plan.v3",
        "ctcc.public.initial_runtime_plan.v4",
        "ctcc.public.runtime_plan.v3",
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
    route = (
        demo_origin.reviewed_demo_public_route(plan["registration_region"])
        if "registration_region" in plan
        else None
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
                *public_v2._plan_pins(route),
            )
        )
        or not _packet_stage_matches(plan, document["barrier_completed_at"], exact=True)
        or public_v2._time(document["started_at"]) <= utc_from_ns(boundary["utc_ns"])
        or public_v2._time(document["completed_at"])
        >= public_v2._time(plan["expires_at"])
    ):
        raise PublicSourceRuntimeError("public_v2_capture_readback_mismatch")
    if "registration_region" in plan and any(
        document.get(name) != plan[name]
        for name in (
            "registration_region",
            "registration_region_authenticated",
            "account_plan_sha256",
            "demo_public_origin_policy_sha256",
            "rest_origin",
            "ws_origin",
        )
    ):
        raise PublicSourceRuntimeError("public_v2_route_readback_mismatch")
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
