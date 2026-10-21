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
import hashlib
import os
import ssl
from dataclasses import dataclass, field
from threading import Lock, get_ident
from typing import Literal
from weakref import WeakKeyDictionary, ref

import httpx

from app.domain.native_clock import native_os_clock, native_stamp, validate_os_clock
from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.exchange.okx.private_rest import (
    OkxDemoPrivateRestClient,
)
from app.trade_evidence.storage import _PosixDirectory, _WindowsDirectory
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_public_origin import reviewed_demo_public_route
from app.trade_qualification.demo_session_route_preflight import (
    preflight_demo_session_route,
)

_MAX_CONFIG_BYTES = 8192
_MAX_CONFIG_WALL_SECONDS = 5
_LEASE_NS = 5_000_000_000
_CONFIG_PATH = "/api/v5/account/config"
_LEASES: WeakKeyDictionary = WeakKeyDictionary()
_PROBES: WeakKeyDictionary = WeakKeyDictionary()
_PROBE_LOCK = Lock()
_ISSUER = object()
_STARTED = object()
_SPENT = object()
_OWNED_ROUTES: WeakKeyDictionary = WeakKeyDictionary()
_PRIVATE_ATTEMPTS: WeakKeyDictionary = WeakKeyDictionary()
_ATTEMPT_SAFE_HEADERS = frozenset(
    {"content-type", "content-length", "content-encoding", "date"}
)
_ATTEMPT_FAILURE_CODES = frozenset(
    {
        "demo_v4_response_invalid",
        "demo_v4_native_tls_required",
        "demo_v4_cleanup_failed",
        "demo_v4_clock_invalid",
        "demo_v4_config_invalid",
        "demo_v4_probe_unavailable",
        "demo_v4_request_invalid",
        "demo_v4_session_invalid",
        "demo_v4_account_route_rejected_50110",
        "demo_v4_account_route_rejected_50119",
    }
)


class _OwnedInitialRouteV4:
    """Private identity; an instance without registry state grants nothing."""

    __slots__ = ("__weakref__",)

    def __copy__(self):
        raise DemoOperationalRouteV4Error("demo_v4_route_not_transferable")

    def __deepcopy__(self, memo):
        raise DemoOperationalRouteV4Error("demo_v4_route_not_transferable")

    def __reduce_ex__(self, protocol):
        raise DemoOperationalRouteV4Error("demo_v4_route_not_transferable")


class DemoOperationalRouteV4Error(ValueError):
    """Static local rejection only; no credential, UID, URL, or remote text."""


def _native_attempt_directory(directory):
    if type(directory) not in (_PosixDirectory, _WindowsDirectory):
        raise DemoOperationalRouteV4Error("demo_v4_private_attempt_directory_required")
    return directory


def _private_attempt(session, private_client):
    state = _PRIVATE_ATTEMPTS.get(session)
    if state is None:
        return None
    if (
        state["task"] is not asyncio.current_task()
        or state["loop"] is not asyncio.get_running_loop()
        or state["pid"] != os.getpid()
        or state["thread"] != get_ident()
        or state["client"] is not private_client
    ):
        raise DemoOperationalRouteV4Error("demo_v4_private_attempt_owner_changed")
    return state


def _retain_attempt_chunk(state, chunk):
    if state is None or type(chunk) is not bytes:
        return
    state["observed_bytes"] += len(chunk)
    state["observed_hash"].update(chunk)
    remaining = _MAX_CONFIG_BYTES - len(state["raw_prefix"])
    state["raw_prefix"].extend(chunk[:remaining])


def _attempt_raw_safe(raw, tokens):
    """Omit ambiguous/echoing bytes rather than persist a credential value."""
    try:
        if any(
            name in raw.lower()
            for name in (
                b"ok-access-key",
                b"ok-access-sign",
                b"ok-access-passphrase",
                b"authorization",
            )
        ):
            return False
        collector._no_secrets(raw, tokens)
        if any(
            token[index : index + 8].encode("ascii") in raw
            for token in tokens
            for index in range(max(1, len(token) - 7))
        ):
            return False
        try:
            _document, canon = capture._decode_json(
                raw, limit=_MAX_CONFIG_BYTES, wire=True
            )
        except Exception:  # noqa: BLE001 -- partial/malformed body is evidence, not data
            # Escaped strings in incomplete JSON cannot be safely decoded for
            # credential-echo screening. Retain their digest/size only.
            return b"\\" not in raw
        collector._no_secret_json(canon, tokens)
        return True
    except Exception:  # noqa: BLE001 -- no echoed credential is written to disk
        return False


def _finalize_private_attempt(state, error):
    directory = state["directory"]
    raw = bytes(state["raw_prefix"])
    safe = _attempt_raw_safe(raw, state["tokens"])
    raw_name = None
    if safe and state["observed_bytes"]:
        raw_name = "account-route-attempt.v4.raw"
        directory.publish(raw_name, raw)
        if directory.read(raw_name, _MAX_CONFIG_BYTES) != raw:
            raise DemoOperationalRouteV4Error("demo_v4_private_attempt_readback_failed")
    if state.get("terminal_code") is not None:
        code = state["terminal_code"]
    elif error is None:
        code = "accepted"
    elif isinstance(error, asyncio.CancelledError):
        code = "cancelled"
    elif isinstance(error, TimeoutError):
        code = "timeout"
    elif (
        type(error) is DemoOperationalRouteV4Error
        and len(error.args) == 1
        and error.args[0] in _ATTEMPT_FAILURE_CODES
    ):
        code = error.args[0]
    else:
        code = "source_or_transport_rejected"
    safe_headers = []
    omitted_headers = 0
    for name, value in state["safe_headers"]:
        if not _attempt_raw_safe(value.encode("utf-8"), state["tokens"]):
            omitted_headers += 1
        else:
            safe_headers.append([name, value])
    document = {
        "schema_version": "ctcc.private_demo_route_attempt.v4",
        "plan_sha256": state["plan_sha256"],
        "disposition": "accepted" if error is None and safe else "rejected",
        "code": code if safe else "credential_safety_raw_omitted",
        "status": state["status"],
        "safe_response_headers": safe_headers,
        "omitted_header_count": omitted_headers,
        "request_start": state["request_start"],
        "headers_received": state["headers_received"],
        "body_completed": state["body_completed"],
        "body_stream_exhausted": state["body_stream_exhausted"],
        "tls_peer_sha256": state["tls_peer_sha256"],
        "observed_bytes": state["observed_bytes"],
        "observed_sha256": state["observed_hash"].hexdigest(),
        "raw_name": raw_name,
        "retained_bytes": len(raw) if raw_name else 0,
        "raw_retained": raw_name is not None,
        "retained_sha256": sha(raw) if raw_name else None,
        "truncated": state["observed_bytes"] > len(raw),
        "raw_omitted_for_credential_safety": not safe,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
    encoded = canonical(document)
    directory.publish("account-route-attempt.v4.json", encoded)
    if directory.read("account-route-attempt.v4.json", 32768) != encoded:
        raise DemoOperationalRouteV4Error("demo_v4_private_attempt_readback_failed")
    if error is None and not safe:
        raise DemoOperationalRouteV4Error("demo_v4_credential_echo_rejected")


async def _probe_initial_demo_operational_route_retained_v4(
    session, private_client, *, proof_directory
):
    """Source-owned private attempt evidence; no injected network/clock seam."""
    _plan, route = _checked_objects(session, private_client)
    directory = _native_attempt_directory(proof_directory)
    if session in _PRIVATE_ATTEMPTS:
        raise DemoOperationalRouteV4Error("demo_v4_private_attempt_already_active")
    encoded = canonical(
        {
            "schema_version": "ctcc.private_demo_route_attempt_plan.v4",
            "account_plan_sha256": session._pin,
            "environment": "demo",
            "route_region": route.registration_region,
            "origin": route.rest_origin,
            "method": "GET",
            "endpoint": _CONFIG_PATH,
            "max_response_bytes": _MAX_CONFIG_BYTES,
            "retries": 0,
            "private_account_evidence": True,
            "execution_authority": False,
        }
    )
    try:
        directory.publish("account-route-attempt.v4.plan.json", encoded)
        if directory.read("account-route-attempt.v4.plan.json", 32768) != encoded:
            raise DemoOperationalRouteV4Error("demo_v4_private_attempt_readback_failed")
    except Exception:  # noqa: BLE001 -- pre-existing evidence is never replaced
        raise DemoOperationalRouteV4Error(
            "demo_v4_private_attempt_storage_failed"
        ) from None
    state = {
        "directory": directory,
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "client": private_client,
        "tokens": collector._credential_values(session._credentials)[:3],
        "plan_sha256": sha(encoded),
        "raw_prefix": bytearray(),
        "observed_bytes": 0,
        "observed_hash": hashlib.sha256(),
        "status": None,
        "safe_headers": (),
        "request_start": None,
        "headers_received": None,
        "body_completed": None,
        "body_stream_exhausted": False,
        "tls_peer_sha256": None,
    }
    _PRIVATE_ATTEMPTS[session] = state
    error = observation = None
    try:
        observation = await probe_initial_demo_operational_route_v4(
            session, private_client
        )
        if (
            state["observed_bytes"] != len(state["raw_prefix"])
            or not state["body_stream_exhausted"]
            or sha(bytes(state["raw_prefix"])) != observation.config_raw_sha256
        ):
            raise DemoOperationalRouteV4Error("demo_v4_private_attempt_body_incomplete")
        return observation
    except BaseException as caught:
        error = caught
        raise
    finally:
        _PRIVATE_ATTEMPTS.pop(session, None)
        if error is not None and observation is not None:
            _LEASES.pop(observation._lease, None)
            _PROBES[session] = _SPENT
        try:
            _finalize_private_attempt(state, error)
        except Exception:  # noqa: BLE001 -- failed retention cannot leave a usable lease
            if observation is not None:
                _LEASES.pop(observation._lease, None)
            lease = _PROBES.get(session)
            if type(lease) is _InitialRouteLeaseV4:
                _LEASES.pop(lease, None)
            _PROBES[session] = _SPENT
            if isinstance(error, asyncio.CancelledError):
                raise error
            raise DemoOperationalRouteV4Error(
                "demo_v4_private_attempt_storage_failed"
            ) from None


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


async def _read_native_config_body(
    *,
    client,
    request,
    context,
    route,
    session,
    private_client,
    plan,
    started,
    holder,
    raw,
):
    """Read one bounded signed config response under the caller's wall deadline."""
    response = await client.send(
        request, auth=None, follow_redirects=False, stream=True
    )
    holder["response"] = response
    attempt = _private_attempt(session, private_client)
    if attempt is not None and type(response) is httpx.Response:
        attempt["status"] = response.status_code
        attempt["safe_headers"] = tuple(
            (name, value[:1024])
            for name, value in response.headers.multi_items()
            if name.lower() in _ATTEMPT_SAFE_HEADERS
        )[:16]
    headers = native_stamp()
    if attempt is not None:
        attempt["headers_received"] = headers
    validate_stamps((started, headers))
    if (
        type(response) is not httpx.Response
        or response.url != request.url
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
    if attempt is not None:
        attempt["tls_peer_sha256"] = sha(certificate)
    if (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
        or response.headers.get("content-encoding", "identity").lower() != "identity"
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
        _retain_attempt_chunk(attempt, chunk)
        _checked_objects(session, private_client)
        if type(chunk) is not bytes or len(raw) + len(chunk) > _MAX_CONFIG_BYTES:
            raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
        raw.extend(chunk)
    if attempt is not None:
        attempt["body_stream_exhausted"] = True
    body_completed = native_stamp()
    if attempt is not None:
        attempt["body_completed"] = body_completed
    validate_stamps((started, headers, body_completed))
    if length is not None and len(raw) != int(length):
        raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
    if response.status_code != 200:
        auth_code = _read_only_auth_rejection(response.status_code, bytes(raw))
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
    return headers, body_completed, sha(certificate)


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
    attempt = _private_attempt(session, private_client)
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
        if attempt is not None:
            attempt["request_start"] = started
            # A reflected HMAC is a private header value too. Never retain it,
            # even when the API key/secret/passphrase themselves are absent.
            attempt["tokens"] = (*attempt["tokens"], _signature)
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
        holder = {"response": None}
        pending_cancel = False
        cleanup_failed = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(plan.max_request_seconds, _MAX_CONFIG_WALL_SECONDS)
        client = _native_client()
        try:
            # The single monotonic deadline covers client enter, send and every
            # raw-body chunk. The two later closes each have their own one-second
            # bounded shield so cancellation cannot abandon a live connection.
            async with asyncio.timeout_at(deadline):
                await client.__aenter__()
                collector._client_guard(client)
                # MockTransport may be used by account fixtures but can never
                # issue an operational route lease here.
                if type(client._transport) is not httpx.AsyncHTTPTransport:
                    raise DemoOperationalRouteV4Error("demo_v4_native_tls_required")
                context = client._transport._pool._ssl_context
                checked_plan, checked_route = _checked_objects(session, private_client)
                validate_os_clock(native_os_clock())
                if (
                    checked_route != route
                    or checked_plan.origin != route.rest_origin
                    or loop.time() >= deadline
                    or task.cancelling()
                ):
                    raise DemoOperationalRouteV4Error("demo_v4_request_invalid")
                headers, body_completed, peer_sha256 = await _read_native_config_body(
                    client=client,
                    request=request,
                    context=context,
                    route=route,
                    session=session,
                    private_client=private_client,
                    plan=plan,
                    started=started,
                    holder=holder,
                    raw=raw,
                )
                if client.cookies:
                    raise DemoOperationalRouteV4Error("demo_v4_response_invalid")
        except asyncio.CancelledError:
            pending_cancel = True
            raise
        finally:
            for resource in (holder["response"], client):
                if resource is None:
                    continue
                try:
                    await collector._close(resource)
                except asyncio.CancelledError:
                    pending_cancel = True
                except Exception:  # noqa: BLE001 -- no transport detail escapes
                    cleanup_failed = True
            if pending_cancel:
                raise asyncio.CancelledError
            if cleanup_failed:
                raise DemoOperationalRouteV4Error("demo_v4_cleanup_failed")
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
            "request_start": started,
            "headers_received": headers,
            "body_completed": body_completed,
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
    except Exception as error:  # noqa: BLE001 -- no raw response, secret, UID or URL escapes
        if attempt is not None and isinstance(error, TimeoutError):
            attempt["terminal_code"] = "timeout"
        raise DemoOperationalRouteV4Error("demo_v4_probe_unavailable") from None


def _consume_initial_route_state_v4(observation, session, private_client):
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
        return {**state, "route": route, "consumed": now}
    except Exception:  # noqa: BLE001 -- spent lease stays spent on every failure
        raise DemoOperationalRouteV4Error("demo_v4_lease_unavailable") from None


def _consume_initial_route_v4(observation, session, private_client):
    """Historical time diagnostic consumes only its route declaration."""
    return _consume_initial_route_state_v4(observation, session, private_client)[
        "route"
    ]


def _claim_initial_route_v4(observation, session, private_client, *, invocation):
    """Move native account bytes into this invocation's bounded public owner."""
    state = _consume_initial_route_state_v4(observation, session, private_client)
    claim = _OwnedInitialRouteV4()
    _OWNED_ROUTES[claim] = {**state, "invocation": invocation}
    return claim


def _owned_route_state_v4(claim, *, invocation, owner_task):
    """Validate a live claim; worker tasks are checked by their source owner."""
    state = _OWNED_ROUTES.get(claim) if type(claim) is _OwnedInitialRouteV4 else None
    try:
        if (
            state is None
            or state["invocation"] is not invocation
            or state["task"]() is not owner_task
            or owner_task is None
            or owner_task.cancelling()
            or asyncio.current_task().cancelling()
            or asyncio.get_running_loop() is not state["loop"]
            or os.getpid() != state["pid"]
            or get_ident() != state["thread"]
        ):
            raise ValueError
        session, private_client = state["session"](), state["client"]()
        plan, route = _checked_objects(session, private_client)
        if (
            route != state["route"]
            or plan.session_binding_id != state["session_binding_id"]
            or plan.expected_uid != state["uid"]
            or plan.expected_main_uid != state["main_uid"]
            or session._credential_pin != state["credential_pin"]
            or session._pin != state["plan_sha256"]
        ):
            raise ValueError
        _decode_accepted_config(
            state["raw"], expected_uid=state["uid"], expected_main_uid=state["main_uid"]
        )
        return state
    except Exception:  # noqa: BLE001 -- no private identifiers or transport details
        raise DemoOperationalRouteV4Error("demo_v4_owned_route_unavailable") from None


def _discard_owned_route_v4(claim):
    _OWNED_ROUTES.pop(claim, None)
