"""Account-first Demo route observation for a future V4 initial public source.

Only a native, signed GET of account/config can issue the in-memory, one-use
initial-route lease. An accepted host proves that this credential session can
read its exact account on that operational route; it does not reveal the
account's registration site or authenticate later public market bytes.

This module deliberately has no public transport, G12 continuation, portfolio,
reservation, intent, or order-writer entrypoint. V2/V3 DENY paths are unchanged.
"""

from __future__ import annotations

import asyncio
import os
import ssl
from dataclasses import dataclass, field
from threading import Lock, get_ident
from typing import Literal
from weakref import WeakKeyDictionary, ref

import httpx

from app.domain.native_clock import native_os_clock, native_stamp, validate_os_clock
from app.domain.source_primitives import sha, utc_from_ns, validate_stamps
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_public_origin import reviewed_demo_public_route
from app.trade_qualification.demo_session_route_preflight import (
    preflight_demo_session_route,
)

_MAX_CONFIG_BYTES = 8192
_LEASE_NS = 5_000_000_000
_CONFIG_PATH = "/api/v5/account/config"
_LEASES: WeakKeyDictionary = WeakKeyDictionary()
_PROBES: WeakKeyDictionary = WeakKeyDictionary()
_PROBE_LOCK = Lock()
_ISSUER = object()
_STARTED = object()
_SPENT = object()


class DemoOperationalRouteV4Error(ValueError):
    """Static local rejection only; no credential, UID, URL, or remote text."""


class _InitialRouteLeaseV4:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise DemoOperationalRouteV4Error("demo_v4_lease_unavailable")

    def __copy__(self):
        raise DemoOperationalRouteV4Error("demo_v4_lease_not_transferable")

    def __deepcopy__(self, memo):
        raise DemoOperationalRouteV4Error("demo_v4_lease_not_transferable")

    def __reduce_ex__(self, protocol):
        raise DemoOperationalRouteV4Error("demo_v4_lease_not_transferable")


@dataclass(frozen=True, slots=True, weakref_slot=True, repr=False)
class InitialOperationalRouteObservationV4:
    """In-memory initial-route diagnostic, never a public source receipt."""

    _lease: _InitialRouteLeaseV4 = field(repr=False)
    route_region: str
    config_raw_sha256: str
    tls_peer_sha256: str
    request_start_ns: int
    headers_received_ns: int
    body_completed_ns: int
    validated_ns: int
    schema_version: Literal["ctcc.demo_initial_operational_route.v4"] = field(
        default="ctcc.demo_initial_operational_route.v4", init=False
    )
    registration_site: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)
    operational_route_verified: Literal[True] = field(default=True, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    public_source_authenticity_verified: Literal[False] = field(
        default=False, init=False
    )
    post_g12_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)

    def __post_init__(self):
        if (
            type(self._lease) is not _InitialRouteLeaseV4
            or _LEASES.get(self._lease) is None
        ):
            raise DemoOperationalRouteV4Error("demo_v4_observation_unavailable")


@dataclass(frozen=True, slots=True)
class _ConfigSpec:
    origin: str
    endpoint: str = _CONFIG_PATH
    parameters: tuple = ()


def _checked_objects(session, private_client):
    if (
        type(session) is not ControlledDemoAccountSession
        or type(private_client) is not OkxDemoPrivateRestClient
        or session._used is not False
    ):
        raise DemoOperationalRouteV4Error("demo_v4_session_invalid")
    try:
        preflight_demo_session_route(session, private_client)
        plan = capture._checked_plan(session._plan, session._pin)
        session._checked_read_session(plan)
        route = reviewed_demo_public_route(plan.registration_region)
        if (
            plan.origin != route.rest_origin
            or session._credential_pin
            != collector._credential_content_pin(session._credentials)
        ):
            raise ValueError
        return plan, route
    except Exception:  # noqa: BLE001 -- no private identity or settings escape
        raise DemoOperationalRouteV4Error("demo_v4_session_invalid") from None


def _decode_accepted_config(raw: bytes, *, expected_uid: str, expected_main_uid: str):
    """Validate one exact OKX success envelope without inferring a signup site."""
    try:
        document, _canonical = capture._decode_json(
            raw, limit=_MAX_CONFIG_BYTES, wire=True
        )
        if (
            type(document) is not dict
            or set(document) != {"code", "msg", "data"}
            or document["code"] != "0"
            or document["msg"] != ""
            or type(document["data"]) is not list
            or len(document["data"]) != 1
        ):
            raise ValueError
        row = document["data"][0]
        if (
            type(row) is not dict
            or capture._required_text(row, "uid", identifier=True) != expected_uid
            or capture._required_text(row, "mainUid", identifier=True)
            != expected_main_uid
            or capture._required_text(row, "acctLv") not in {"1", "2", "3", "4"}
            or capture._required_text(row, "posMode")
            not in {"net_mode", "long_short_mode"}
        ):
            raise ValueError
    except Exception:  # noqa: BLE001 -- never include remote body or account IDs
        raise DemoOperationalRouteV4Error("demo_v4_config_invalid") from None


def _read_only_auth_rejection(status: int, raw: bytes) -> str | None:
    """Expose only two reviewed status codes, never remote msg or IP text."""
    if status != 401 or len(raw) > 4096:
        return None
    try:
        document, _canonical = capture._decode_json(raw, limit=4096, wire=True)
        if type(document) is dict and document.get("code") in {"50110", "50119"}:
            return document["code"]
    except Exception:  # noqa: BLE001 -- malformed remote errors remain opaque
        return None
    return None


def _native_client():
    return collector._new_client()


async def probe_initial_demo_operational_route_v4(
    session: ControlledDemoAccountSession,
    private_client: OkxDemoPrivateRestClient,
) -> InitialOperationalRouteObservationV4:
    """Perform exactly one signed native Demo config GET before public IO.

    This is the *initial* pre-G12 route probe. No caller-provided barrier is
    accepted; a post-G12 V4 path must obtain a new, owned G12 barrier and a
    fresh account observation. Mock or injected transport cannot issue a lease.
    """
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise DemoOperationalRouteV4Error("demo_v4_task_invalid")
    plan, route = _checked_objects(session, private_client)
    # An account session gets at most one route probe, including failures.
    # This synchronous claim precedes clock sampling and all network IO.
    with _PROBE_LOCK:
        if session in _PROBES:
            raise DemoOperationalRouteV4Error("demo_v4_session_already_probed")
        _PROBES[session] = _STARTED
    try:
        validate_os_clock(native_os_clock())
        started = native_stamp()
        request, _signature = collector._signed_request(
            _ConfigSpec(plan.origin),
            session._credentials,
            utc_from_ns(started["utc_ns"]),
            min(plan.max_request_seconds, 5),
        )
        if (
            request.method != "GET"
            or request.url != httpx.URL(route.rest_origin + _CONFIG_PATH)
            or request.url.query
            or request.content
            or request.headers.get("x-simulated-trading") != "1"
            or request.headers.get("host") != route.rest_hostname
        ):
            raise DemoOperationalRouteV4Error("demo_v4_request_invalid")
        raw = bytearray()
        response = None
        pending_cancel = False
        async with _native_client() as client:
            collector._client_guard(client)
            # Account collector permits MockTransport for synthetic records;
            # this issuer cannot turn such a record into an operational lease.
            if type(client._transport) is not httpx.AsyncHTTPTransport:
                raise DemoOperationalRouteV4Error("demo_v4_native_tls_required")
            context = client._transport._pool._ssl_context
            _checked_objects(session, private_client)
            try:
                response = await client.send(
                    request, auth=None, follow_redirects=False, stream=True
                )
                headers = native_stamp()
                validate_stamps((started, headers))
                if (
                    response.url != request.url
                    or response.history
                    or response.is_closed
                    or response.is_stream_consumed
                    or "set-cookie" in response.headers
                    or client.cookies
                ):
                    raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
                network = response.extensions.get("network_stream")
                tls = None if network is None else network.get_extra_info("ssl_object")
                if (
                    type(tls) is not ssl.SSLObject
                    or tls.context is not context
                    or tls.server_hostname != route.rest_hostname
                    or tls.version() not in {"TLSv1.2", "TLSv1.3"}
                ):
                    raise DemoOperationalRouteV4Error("demo_v4_native_tls_required")
                certificate = tls.getpeercert(binary_form=True)
                if type(certificate) is not bytes or not certificate:
                    raise DemoOperationalRouteV4Error("demo_v4_native_tls_required")
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                    or response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isascii()
                    or not length.isdigit()
                    or len(length) > 5
                    or int(length) > _MAX_CONFIG_BYTES
                ):
                    raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
                async for chunk in response.aiter_raw():
                    _checked_objects(session, private_client)
                    if (
                        type(chunk) is not bytes
                        or len(raw) + len(chunk) > _MAX_CONFIG_BYTES
                    ):
                        raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
                    raw.extend(chunk)
                body_completed = native_stamp()
                validate_stamps((started, headers, body_completed))
                if length is not None and len(raw) != int(length):
                    raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
                if response.status_code != 200:
                    auth_code = _read_only_auth_rejection(
                        response.status_code, bytes(raw)
                    )
                    if auth_code in {"50110", "50119"}:
                        raise DemoOperationalRouteV4Error(
                            "demo_v4_account_route_rejected_" + auth_code
                        )
                    raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
                tokens = collector._credential_values(session._credentials)[:3]
                collector._no_secrets(bytes(raw), tokens)
                document, canon = capture._decode_json(
                    bytes(raw), limit=_MAX_CONFIG_BYTES, wire=True
                )
                collector._no_secret_json(canon, tokens)
                _decode_accepted_config(
                    bytes(raw),
                    expected_uid=plan.expected_uid,
                    expected_main_uid=plan.expected_main_uid,
                )
                if document["data"][0]["uid"] != plan.expected_uid:
                    raise DemoOperationalRouteV4Error("demo_v4_config_invalid")
                peer_sha256 = sha(certificate)
            except asyncio.CancelledError:
                pending_cancel = True
                raise
            finally:
                if response is not None:
                    await collector._close(response, pending_cancel=pending_cancel)
            if client.cookies:
                raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
        validated = native_stamp()
        validate_stamps((started, headers, body_completed, validated))
        if (
            validated["monotonic_ns"] - started["monotonic_ns"] > 10_000_000_000
            or task.cancelling()
        ):
            raise DemoOperationalRouteV4Error("demo_v4_clock_invalid")
        _checked_objects(session, private_client)
        lease = _InitialRouteLeaseV4(_ISSUER)
        _LEASES[lease] = {
            "task": ref(task),
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": get_ident(),
            "session": ref(session),
            "client": ref(private_client),
            "session_binding_id": plan.session_binding_id,
            "credential_pin": session._credential_pin,
            "plan_sha256": session._pin,
            "origin": route.rest_origin,
            "uid": plan.expected_uid,
            "main_uid": plan.expected_main_uid,
            "validated": validated,
            "raw": bytes(raw),
            "peer_sha256": peer_sha256,
            "request_start_ns": started["utc_ns"],
            "headers_received_ns": headers["utc_ns"],
            "body_completed_ns": body_completed["utc_ns"],
            "validated_ns": validated["utc_ns"],
        }
        observation = InitialOperationalRouteObservationV4(
            _lease=lease,
            route_region=route.registration_region,
            config_raw_sha256=sha(bytes(raw)),
            tls_peer_sha256=peer_sha256,
            request_start_ns=started["utc_ns"],
            headers_received_ns=headers["utc_ns"],
            body_completed_ns=body_completed["utc_ns"],
            validated_ns=validated["utc_ns"],
        )
        _LEASES[lease]["observation"] = ref(observation)
        with _PROBE_LOCK:
            if _PROBES.get(session) is not _STARTED:
                raise DemoOperationalRouteV4Error("demo_v4_session_invalid")
            _PROBES[session] = lease
        return observation
    except DemoOperationalRouteV4Error:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no raw response, secret, UID or URL escapes
        raise DemoOperationalRouteV4Error("demo_v4_probe_unavailable") from None


def _consume_initial_route_v4(observation, session, private_client):
    """Burn one route lease for a future owned *initial* V4 public issuer.

    The result is a reviewed route declaration, not permission to send a
    request, source authentication, or a post-G12 barrier. This intentionally
    has no current caller in the V2/V3 source runtime.
    """
    lease = (
        observation._lease
        if type(observation) is InitialOperationalRouteObservationV4
        else None
    )
    with _PROBE_LOCK:
        state = (
            _LEASES.pop(lease, None) if type(lease) is _InitialRouteLeaseV4 else None
        )
        probe_matches = (
            type(session) is ControlledDemoAccountSession
            and _PROBES.get(session) is lease
        )
        if probe_matches:
            _PROBES[session] = _SPENT
    try:
        task = asyncio.current_task()
        if (
            state is None
            or not probe_matches
            or task is None
            or task.cancelling()
            or task is not state["task"]()
            or asyncio.get_running_loop() is not state["loop"]
            or os.getpid() != state["pid"]
            or get_ident() != state["thread"]
            or session is not state["session"]()
            or private_client is not state["client"]()
            or observation is not state["observation"]()
        ):
            raise ValueError
        plan, route = _checked_objects(session, private_client)
        now = native_stamp()
        validate_stamps((state["validated"], now))
        if (
            now["monotonic_ns"] - state["validated"]["monotonic_ns"] >= _LEASE_NS
            or now["utc_ns"] - state["validated"]["utc_ns"] >= _LEASE_NS
            or plan.session_binding_id != state["session_binding_id"]
            or session._credential_pin != state["credential_pin"]
            or session._pin != state["plan_sha256"]
            or route.rest_origin != state["origin"]
            or plan.expected_uid != state["uid"]
            or plan.expected_main_uid != state["main_uid"]
            or sha(state["raw"]) != observation.config_raw_sha256
            or observation.tls_peer_sha256 != state["peer_sha256"]
            or observation.request_start_ns != state["request_start_ns"]
            or observation.headers_received_ns != state["headers_received_ns"]
            or observation.body_completed_ns != state["body_completed_ns"]
            or observation.validated_ns != state["validated_ns"]
            or observation.route_region != route.registration_region
            or observation.registration_site != "UNKNOWN"
            or observation.operational_route_verified is not True
            or observation.account_complete is not False
            or observation.public_source_authenticity_verified is not False
            or observation.post_g12_eligible is not False
            or observation.execution_authority is not False
            or observation.admission != "DENY"
            or observation.schema_version != "ctcc.demo_initial_operational_route.v4"
        ):
            raise ValueError
        return route
    except Exception:  # noqa: BLE001 -- spent lease stays spent on every failure
        raise DemoOperationalRouteV4Error("demo_v4_lease_unavailable") from None
