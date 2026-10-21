"""One account-first, DENY-only Demo public time observation.

The signed account/config issuer and its one-use lease are owned by V4. This
module consumes that lease in the issuing task before opening any public
transport. It does not mint a market source, initial qualification, G12
continuation, or order authority. Public bytes remain in memory only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.domain.native_clock import native_os_clock, native_stamp, validate_os_clock
from app.domain.source_primitives import sha, validate_stamps
from app.trade_qualification import account_collector as account_io
from app.trade_qualification import demo_operational_route_v4 as route_v4
from app.trade_qualification import demo_public_time_route_diagnostic as time_diag
from app.trade_qualification.demo_public_origin import (
    _checked_route,
    build_demo_public_time_request_v3,
    validate_demo_public_time_request_v3,
    validate_demo_public_time_response_v3,
)

_MAX_BODY = 4096
_MAX_ACCOUNT_TO_PUBLIC_NS = 5_000_000_000
_MAX_PUBLIC_WALL_SECONDS = 5


class InitialDemoPublicSourceV4Error(ValueError):
    """Fixed local failure code; never an account, response, or URL string."""


@dataclass(frozen=True, slots=True, repr=False)
class InitialDemoPublicTimeObservationV4:
    """A nonportable diagnostic record, not a trusted source receipt."""

    route_region: str
    account_config_raw_sha256: str
    account_tls_peer_sha256: str
    public_raw_body: bytes = field(repr=False)
    public_raw_sha256: str
    public_canonical_sha256: str
    public_tls_peer_sha256: str
    account_validated_ns: int
    lease_consumed_ns: int
    request_start_ns: int
    headers_received_ns: int
    body_completed_ns: int
    validated_ns: int
    server_time_ms: int
    schema_version: Literal["ctcc.demo_initial_public_time.v4"] = field(
        default="ctcc.demo_initial_public_time.v4", init=False
    )
    registration_site: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)
    durable_custody: Literal[False] = field(default=False, init=False)
    public_source_authenticity_verified: Literal[False] = field(
        default=False, init=False
    )
    account_complete: Literal[False] = field(default=False, init=False)
    post_g12_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _time_client() -> httpx.AsyncClient:
    """A fresh native client; no caller/client injection is an API input."""
    return time_diag._client()


def _causal_time(
    account: route_v4.InitialOperationalRouteObservationV4,
    consumed: dict,
    started: dict,
    headers: dict,
    body: dict,
    validated: dict,
    server_time_ms: int,
) -> None:
    validate_stamps((consumed, started, headers, body, validated))
    if (
        type(server_time_ms) is not int
        or consumed["utc_ns"] <= account.validated_ns
        or started["utc_ns"] <= consumed["utc_ns"]
        or started["monotonic_ns"] <= consumed["monotonic_ns"]
        or not 0 < started["utc_ns"] - account.validated_ns < _MAX_ACCOUNT_TO_PUBLIC_NS
        or not 0
        < started["monotonic_ns"] - consumed["monotonic_ns"]
        < _MAX_ACCOUNT_TO_PUBLIC_NS
        or not started["utc_ns"] <= server_time_ms * 1_000_000 <= headers["utc_ns"]
        or validated["monotonic_ns"] - started["monotonic_ns"]
        > _MAX_ACCOUNT_TO_PUBLIC_NS
    ):
        raise InitialDemoPublicSourceV4Error("demo_v4_public_time_causality_invalid")


async def capture_initial_demo_public_time_v4(session, private_client):
    """Signed Demo config GET, lease burn, then one native public time GET.

    The caller supplies neither a route nor a reusable account observation.
    This initial-stage function has no post-G12 barrier or market/WS capture.
    """
    try:
        account = await route_v4.probe_initial_demo_operational_route_v4(
            session, private_client
        )
        route = route_v4._consume_initial_route_v4(account, session, private_client)
        consumed = native_stamp()
        if consumed["utc_ns"] <= account.validated_ns:
            raise InitialDemoPublicSourceV4Error(
                "demo_v4_public_time_causality_invalid"
            )
        validate_os_clock(native_os_clock())
        request = build_demo_public_time_request_v3(route, "time_pre")
        response = None
        raw = bytearray()
        client = _time_client()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _MAX_PUBLIC_WALL_SECONDS
        pending_cancel = False
        cleanup_failed = False
        try:
            # Enter and body share one absolute deadline. A slow enter cannot
            # spend the route lease and then wait forever before the GET.
            async with asyncio.timeout_at(deadline):
                await client.__aenter__()
            # The account collector checks native pool proxy/UDS/local-address/
            # socket/backend isolation. The time diagnostic additionally rejects
            # its synthetic MockTransport allowance before any network send.
            account_io._client_guard(client)
            context = time_diag._client_context(client)
            _checked_route(route)
            checked_plan, checked_route = route_v4._checked_objects(
                session, private_client
            )
            if (
                checked_route != route
                or checked_plan.origin != route.rest_origin
                or client.cookies
            ):
                raise InitialDemoPublicSourceV4Error("demo_v4_public_route_changed")
            started = native_stamp()
            validate_stamps((consumed, started))
            if (
                started["utc_ns"] <= account.validated_ns
                or started["utc_ns"] <= consumed["utc_ns"]
                or started["monotonic_ns"] <= consumed["monotonic_ns"]
                or started["utc_ns"] - account.validated_ns >= _MAX_ACCOUNT_TO_PUBLIC_NS
                or started["monotonic_ns"] - consumed["monotonic_ns"]
                >= _MAX_ACCOUNT_TO_PUBLIC_NS
            ):
                raise InitialDemoPublicSourceV4Error(
                    "demo_v4_public_time_causality_invalid"
                )
            try:
                # No await lies between the final session/client/route/clock
                # checks above and this one read-only network send.
                async with asyncio.timeout_at(deadline):
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    account_io._client_guard(client)
                    time_diag._client_context(client)
                    _checked_route(route)
                    validate_os_clock(native_os_clock())
                    checked_plan, checked_route = route_v4._checked_objects(
                        session, private_client
                    )
                    if (
                        checked_route != route
                        or checked_plan.origin != route.rest_origin
                        or account.route_region != route.registration_region
                    ):
                        raise InitialDemoPublicSourceV4Error(
                            "demo_v4_public_route_changed"
                        )
                    validate_demo_public_time_request_v3(route, "time_pre", request)
                    dispatched = native_stamp()
                    validate_stamps((consumed, started, dispatched))
                    if (
                        dispatched["utc_ns"] <= account.validated_ns
                        or loop.time() >= deadline
                        or dispatched["utc_ns"] - account.validated_ns
                        >= _MAX_ACCOUNT_TO_PUBLIC_NS
                        or dispatched["monotonic_ns"] - consumed["monotonic_ns"]
                        >= _MAX_ACCOUNT_TO_PUBLIC_NS
                    ):
                        raise InitialDemoPublicSourceV4Error(
                            "demo_v4_public_time_causality_invalid"
                        )
                    started = dispatched
                    response = await client.send(
                        request, auth=None, follow_redirects=False, stream=True
                    )
                    headers = native_stamp()
                    validate_stamps((started, headers))
                    if (
                        type(response) is not httpx.Response
                        or response.is_closed
                        or response.is_stream_consumed
                        or "set-cookie" in response.headers
                        or client.cookies
                    ):
                        raise InitialDemoPublicSourceV4Error(
                            "demo_v4_public_response_invalid"
                        )
                    validate_demo_public_time_response_v3(
                        route, "time_pre", request, response
                    )
                    tls_proof = time_diag._tls(response, context, route.rest_hostname)
                    safe_headers, bad_headers = time_diag._response_headers(response)
                    safe = dict(safe_headers)
                    if (
                        bad_headers
                        or safe.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                        or safe.get("content-encoding", "identity").lower()
                        != "identity"
                    ):
                        raise InitialDemoPublicSourceV4Error(
                            "demo_v4_public_response_invalid"
                        )
                    length = safe.get("content-length")
                    if length is not None and (
                        not length.isascii()
                        or not length.isdigit()
                        or len(length) > 5
                        or int(length) > _MAX_BODY
                    ):
                        raise InitialDemoPublicSourceV4Error(
                            "demo_v4_public_response_invalid"
                        )
                    async for chunk in response.aiter_raw():
                        if (
                            type(chunk) is not bytes
                            or len(raw) + len(chunk) > _MAX_BODY
                        ):
                            raise InitialDemoPublicSourceV4Error(
                                "demo_v4_public_response_invalid"
                            )
                        raw.extend(chunk)
                    body = native_stamp()
                    validate_stamps((started, headers, body))
                    if length is not None and len(raw) != int(length):
                        raise InitialDemoPublicSourceV4Error(
                            "demo_v4_public_response_invalid"
                        )
            except asyncio.CancelledError:
                pending_cancel = True
                raise
            if client.cookies:
                raise InitialDemoPublicSourceV4Error("demo_v4_public_response_invalid")
        except asyncio.CancelledError:
            pending_cancel = True
            raise
        finally:
            # Both closes run even if cancellation arrives during body streaming
            # or the first close. A failed close cannot produce an observation.
            for resource in (response, client):
                if resource is None:
                    continue
                try:
                    await account_io._close(resource)
                except asyncio.CancelledError:
                    pending_cancel = True
                except Exception:  # noqa: BLE001 -- no transport detail escapes
                    cleanup_failed = True
            if pending_cancel:
                raise asyncio.CancelledError
            if cleanup_failed:
                raise InitialDemoPublicSourceV4Error("demo_v4_public_cleanup_failed")
        validated = native_stamp()
        server_text, canonical_sha = time_diag._server_time(bytes(raw))
        server_ms = int(server_text)
        _causal_time(account, consumed, started, headers, body, validated, server_ms)
        route_v4._checked_objects(session, private_client)
        return InitialDemoPublicTimeObservationV4(
            route_region=route.registration_region,
            account_config_raw_sha256=account.config_raw_sha256,
            account_tls_peer_sha256=account.tls_peer_sha256,
            public_raw_body=bytes(raw),
            public_raw_sha256=sha(bytes(raw)),
            public_canonical_sha256=canonical_sha,
            public_tls_peer_sha256=tls_proof["peer_sha256"],
            account_validated_ns=account.validated_ns,
            lease_consumed_ns=consumed["utc_ns"],
            request_start_ns=started["utc_ns"],
            headers_received_ns=headers["utc_ns"],
            body_completed_ns=body["utc_ns"],
            validated_ns=validated["utc_ns"],
            server_time_ms=server_ms,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no credential, UID, raw body or URL escapes
        raise InitialDemoPublicSourceV4Error(
            "demo_v4_initial_public_time_unavailable"
        ) from None
