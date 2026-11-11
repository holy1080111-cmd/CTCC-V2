"""One bounded, raw Live account-config GET; data only, never account authority.

The opt-in Live lease supplies exact credentials and origin. This observer owns
one request, retains its identity-encoded body privately, and refuses anything
it cannot time and check. It does not issue a complete account snapshot or an
execution permit; durable source custody belongs to a later versioned owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from app.domain import native_clock
from app.domain.source_primitives import (
    ClockStamp,
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.live_read_lease_v1 import (
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.exchange.okx.private_rest import build_signature, utc_iso_timestamp

SCHEMA = "ctcc.live_account_config_raw_observation.v1"
PATH = "/api/v5/account/config"
MAX_BODY = 65536
MAX_HEADERS = 256
MAX_HEADER_BYTES = 32768
MAX_CHUNKS = 65536
MAX_REQUEST_SECONDS = 10.0
_SIGNED_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SIGNED_HEADERS = {
    "Content-Type",
    "Accept",
    "User-Agent",
    "OK-ACCESS-KEY",
    "OK-ACCESS-SIGN",
    "OK-ACCESS-PASSPHRASE",
    "OK-ACCESS-TIMESTAMP",
    "Accept-Encoding",
}
_OWNED_GET_PATHS = frozenset(
    {
        PATH,
        "/api/v5/account/account-position-risk",
        "/api/v5/account/balance",
        "/api/v5/account/positions",
        "/api/v5/trade/orders-pending",
        "/api/v5/trade/orders-algo-pending",
        "/api/v5/account/instruments",
        "/api/v5/account/leverage-info",
    }
)


class LiveConfigObservationError(ValueError):
    """Fixed failure code; never contains a remote body or private setting."""


class _LivePageBodyError(ValueError):
    """A checked current-account page refusal from the shared raw transport."""


def _deny(code: str) -> None:
    raise LiveConfigObservationError(code) from None


def _stamp() -> ClockStamp:
    try:
        return ClockStamp.model_validate(native_clock.native_stamp())
    except Exception:  # noqa: BLE001 - no clock diagnostic or host path may escape
        _deny("live_config_native_clock_unavailable")


def _validate_clock(stamps: tuple[ClockStamp, ...]) -> None:
    try:
        validate_stamps(tuple(item.model_dump() for item in stamps))
    except Exception:  # noqa: BLE001 - fixed clock refusal
        _deny("live_config_clock_sequence_invalid")


def _validate_signed_time(
    signed_at: str, started: ClockStamp, dispatched: ClockStamp
) -> None:
    try:
        if (
            type(signed_at) is not str
            or _SIGNED_TIME.fullmatch(signed_at) is None
            or signed_at != utc_iso_timestamp(utc_from_ns(started.utc_ns))
            or dispatched.utc_ns < started.utc_ns
        ):
            _deny("live_config_signature_clock_invalid")
        # The timestamp is derived exactly from the measured native start.
        # Its millisecond encoding is only an auth field, not an exchange event.
    except LiveConfigObservationError:
        raise
    except Exception:  # noqa: BLE001 - never expose host or signed header text
        _deny("live_config_signature_clock_invalid")


def _header_digest(headers: tuple[tuple[bytes, bytes], ...]) -> str:
    if len(headers) > MAX_HEADERS:
        _deny("live_config_headers_invalid")
    size = 0
    digest = hashlib.sha256()
    for key, value in headers:
        if type(key) is not bytes or type(value) is not bytes:
            _deny("live_config_headers_invalid")
        size += len(key) + len(value)
        if size > MAX_HEADER_BYTES:
            _deny("live_config_headers_invalid")
        for part in (key, value):
            digest.update(len(part).to_bytes(4, "big"))
            digest.update(part)
    return digest.hexdigest()


def _checked_response_headers(
    headers: tuple[tuple[bytes, bytes], ...], *, private_tokens: tuple[str, ...]
) -> int | None:
    _header_digest(headers)
    selected: dict[bytes, bytes] = {}
    for key, value in headers:
        lower = key.lower()
        if lower.startswith(b"ok-access-") or lower in {
            b"authorization",
            b"proxy-authorization",
            b"cookie",
        }:
            _deny("live_config_credential_echo_rejected")
        if lower in {
            b"content-type",
            b"content-encoding",
            b"content-length",
            b"set-cookie",
        }:
            if lower in selected:
                _deny("live_config_headers_invalid")
            selected[lower] = value
        if any(
            token.encode("utf-8") in part
            for part in (key, value)
            for token in private_tokens
        ):
            _deny("live_config_credential_echo_rejected")
    if (
        selected.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        != b"application/json"
        or selected.get(b"content-encoding", b"identity").lower() != b"identity"
        or b"set-cookie" in selected
    ):
        _deny("live_config_http_response_invalid")
    raw_length = selected.get(b"content-length")
    if raw_length is None:
        return None
    if (
        not raw_length.isascii()
        or not raw_length.isdigit()
        or len(raw_length) > len(str(MAX_BODY))
        or int(raw_length) > MAX_BODY
    ):
        _deny("live_config_body_bound")
    return int(raw_length)


def _check_client(client: httpx.AsyncClient, *, synthetic: bool) -> None:
    """Exact owned HTTPX transport; a test mock cannot attest a Live peer."""
    if (
        type(client) is not httpx.AsyncClient
        or client.is_closed
        or client.trust_env
        or client.follow_redirects
        or client.auth is not None
        or client.params
        or client.cookies
        or any(client.event_hooks.values())
        or set(client.headers)
        - {"accept", "accept-encoding", "connection", "user-agent"}
        or type(client._mounts) is not dict
        or any(value is not None for value in client._mounts.values())
    ):
        _deny("live_config_transport_invalid")
    transport = client._transport
    if synthetic:
        if type(transport) is not httpx.MockTransport:
            _deny("live_config_transport_invalid")
        return
    if type(transport) is not httpx.AsyncHTTPTransport:
        _deny("live_config_transport_invalid")
    pool = transport._pool
    if (
        type(pool) is not httpcore.AsyncConnectionPool
        or type(pool._retries) is not int
        or pool._retries != 0
        or pool._proxy is not None
        or pool._uds is not None
        or pool._local_address is not None
        or pool._socket_options is not None
        or type(pool._network_backend) is not AutoBackend
        or type(pool._ssl_context) is not ssl.SSLContext
        or pool._ssl_context.verify_mode != ssl.CERT_REQUIRED
        or pool._ssl_context.check_hostname is not True
    ):
        _deny("live_config_transport_invalid")


def _tls_certificate_hash(
    response: httpx.Response, client: httpx.AsyncClient, *, synthetic: bool
) -> str | None:
    if synthetic:
        return None
    try:
        network = response.extensions.get("network_stream")
        tls = None if network is None else network.get_extra_info("ssl_object")
        if (
            type(tls) is not ssl.SSLObject
            or tls.context is not client._transport._pool._ssl_context
            or tls.server_hostname != response.request.url.host
            or tls.version() not in {"TLSv1.2", "TLSv1.3"}
        ):
            _deny("live_config_tls_evidence_missing")
        certificate = tls.getpeercert(binary_form=True)
        if type(certificate) is not bytes or not certificate:
            _deny("live_config_tls_evidence_missing")
        return sha(certificate)
    except LiveConfigObservationError:
        raise
    except Exception:  # noqa: BLE001 - peer details must not escape
        _deny("live_config_tls_evidence_missing")


def _check_no_credential_echo(value: Any, secrets: tuple[str, ...]) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str and any(secret in item for secret in secrets):
            _deny("live_config_credential_echo_rejected")
        if type(item) is dict:
            pending.extend(item)
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)


def _checked_config(
    raw: bytes, *, lease: LivePinnedGetLeaseV1, signature: str
) -> bytes:
    try:
        payload = decode(raw, maximum=MAX_BODY)
        if type(payload) is not dict or payload.get("code") != "0":
            _deny("live_config_response_invalid")
        rows = payload.get("data")
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            _deny("live_config_row_count_invalid")
        row = rows[0]
        if (
            type(row.get("uid")) is not str
            or type(row.get("mainUid")) is not str
            or row["uid"] != lease._pin.expected_uid
            or row["mainUid"] != lease._pin.expected_main_uid
        ):
            _deny("live_config_account_identity_mismatch")
        private_tokens = (*lease._pin.credentials, signature)
        if any(token.encode("utf-8") in raw for token in private_tokens):
            _deny("live_config_credential_echo_rejected")
        _check_no_credential_echo(payload, private_tokens)
        return canonical(payload)
    except LiveConfigObservationError:
        raise
    except Exception:  # noqa: BLE001 - never forward remote JSON
        _deny("live_config_response_invalid")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class LiveAccountConfigObservationV1:
    """Immutable private raw read; the hash is a diagnostic readback binding."""

    raw_body: bytes = field(repr=False)
    raw_headers: tuple[tuple[bytes, bytes], ...] = field(repr=False)
    signed_at: str
    stamps: tuple[ClockStamp, ...] = field(repr=False)
    lease_binding_id: str = field(repr=False)
    origin: str
    raw_sha256: str
    canonical_sha256: str
    headers_sha256: str
    request_signature_sha256: str
    expected_uid_sha256: str = field(repr=False)
    expected_main_uid_sha256: str = field(repr=False)
    tls_certificate_sha256: str | None
    receipt_sha256: str
    synthetic_mock: bool
    account_complete: bool = field(default=False, init=False)
    source_authenticity_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _receipt_document(observation: LiveAccountConfigObservationV1) -> dict:
    return {
        "schema_version": SCHEMA,
        "origin": observation.origin,
        "method": "GET",
        "path": PATH,
        "lease_binding_id": observation.lease_binding_id,
        "stamps": [item.model_dump() for item in observation.stamps],
        "raw_sha256": observation.raw_sha256,
        "canonical_sha256": observation.canonical_sha256,
        "headers_sha256": observation.headers_sha256,
        "request_signature_sha256": observation.request_signature_sha256,
        "signed_at": observation.signed_at,
        "expected_uid_sha256": observation.expected_uid_sha256,
        "expected_main_uid_sha256": observation.expected_main_uid_sha256,
        "tls_certificate_sha256": observation.tls_certificate_sha256,
        "synthetic_mock": observation.synthetic_mock,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }


def verify_live_account_config_readback_v1(
    observation: LiveAccountConfigObservationV1, *, lease: LivePinnedGetLeaseV1
) -> dict:
    """Pure in-memory readback of raw/hash/clock/identity; never durability."""
    if type(observation) is not LiveAccountConfigObservationV1:
        _deny("live_config_readback_invalid")
    lease.verify_current()
    if (
        observation.lease_binding_id != lease.binding_id
        or observation.origin != lease._pin.origin
        or observation.expected_uid_sha256 != sha(lease._pin.expected_uid.encode())
        or observation.expected_main_uid_sha256
        != sha(lease._pin.expected_main_uid.encode())
        or observation.account_complete is not False
        or observation.source_authenticity_verified is not False
        or observation.execution_authority is not False
        or len(observation.stamps) != 6
        or (observation.tls_certificate_sha256 is None)
        is not observation.synthetic_mock
        or (
            observation.tls_certificate_sha256 is not None
            and (
                type(observation.tls_certificate_sha256) is not str
                or _DIGEST.fullmatch(observation.tls_certificate_sha256) is None
            )
        )
    ):
        _deny("live_config_readback_invalid")
    _validate_clock(observation.stamps)
    _validate_signed_time(
        observation.signed_at, observation.stamps[0], observation.stamps[1]
    )
    expected_signature = build_signature(
        timestamp=observation.signed_at,
        method="GET",
        request_path=PATH,
        body="",
        secret=lease._pin.credentials[1],
    )
    declared_length = _checked_response_headers(
        observation.raw_headers,
        private_tokens=(*lease._pin.credentials, expected_signature),
    )
    if declared_length is not None and len(observation.raw_body) != declared_length:
        _deny("live_config_readback_invalid")
    if (
        sha(observation.raw_body) != observation.raw_sha256
        or sha(
            _checked_config(
                observation.raw_body,
                lease=lease,
                signature=expected_signature,
            )
        )
        != observation.canonical_sha256
        or _header_digest(observation.raw_headers) != observation.headers_sha256
        or sha(
            canonical(
                {
                    "method": "GET",
                    "url": observation.origin + PATH,
                    "signature": expected_signature,
                }
            )
        )
        != observation.request_signature_sha256
        or sha(canonical(_receipt_document(observation))) != observation.receipt_sha256
    ):
        _deny("live_config_readback_invalid")
    return {
        "schema_version": SCHEMA,
        "receipt_sha256": observation.receipt_sha256,
        "raw_sha256": observation.raw_sha256,
        "synthetic_mock": observation.synthetic_mock,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }


async def _close_once(resource: Any) -> None:
    async def once() -> None:
        async with asyncio.timeout(2.0):
            await resource.aclose()

    task = asyncio.create_task(once())
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:  # noqa: BLE001 - inspected below without details
            break
    try:
        task.result()
    except Exception:  # noqa: BLE001 - no transport exception message escapes
        _deny("live_config_cleanup_failed")
    if interrupted:
        raise asyncio.CancelledError


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _OwnedRawGetV1:
    raw_body: bytes = field(repr=False)
    raw_headers: tuple[tuple[bytes, bytes], ...] = field(repr=False)
    canonical_body: bytes = field(repr=False)
    signed_at: str
    stamps: tuple[ClockStamp, ...]
    request_signature_sha256: str
    tls_certificate_sha256: str | None
    synthetic_mock: bool


async def _observe_owned_live_get_v1(
    client: OkxLivePinnedGetClientV1,
    lease: LivePinnedGetLeaseV1,
    *,
    path: str,
    request_path: str,
    validate_body: Callable[[bytes, LivePinnedGetLeaseV1, str], bytes],
    selector: object | None = None,
) -> _OwnedRawGetV1:
    """Single signed, bounded, native-clock Live GET transport for fixed sources."""
    if (
        type(client) is not OkxLivePinnedGetClientV1
        or type(lease) is not LivePinnedGetLeaseV1
    ):
        _deny("live_config_owner_invalid")
    if lease is not client._current_lease():
        _deny("live_config_owner_invalid")
    if (
        type(path) is not str
        or path not in _OWNED_GET_PATHS
        or type(request_path) is not str
        or not (
            request_path == path
            or (
                request_path.startswith(path + "?")
                and re.fullmatch(r"[A-Za-z0-9/%.,_?=&-]+", request_path) is not None
            )
        )
        or not callable(validate_body)
    ):
        _deny("live_config_signed_request_invalid")
    if path == PATH:
        if request_path != PATH or selector is not None:
            _deny("live_config_signed_request_invalid")
    else:
        # The shared transport cannot widen its target by an unchecked caller.
        from app.exchange.okx.live_account_raw_page_v1 import (
            _checked_selector,
            _request_path,
        )

        approved = _checked_selector(selector)
        if approved.path != path or _request_path(approved) != request_path:
            _deny("live_config_signed_request_invalid")
    synthetic = client.settings.environment == "test"
    own_client = not synthetic
    http = None
    response = None
    try:
        http = (
            httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(
                    verify=True, trust_env=False, retries=0
                ),
                trust_env=False,
                follow_redirects=False,
                auth=None,
                timeout=httpx.Timeout(
                    min(lease._pin.timeout_seconds, MAX_REQUEST_SECONDS)
                ),
            )
            if own_client
            else client._external_client
        )
        _check_client(http, synthetic=synthetic)
        lease.verify_current()
        request_budget = min(lease._pin.timeout_seconds, MAX_REQUEST_SECONDS)
        loop = asyncio.get_running_loop()
        loop_deadline = loop.time() + request_budget
        started = _stamp()
        native_deadline_ns = started.monotonic_ns + int(request_budget * 1e9)
        signed_at = utc_iso_timestamp(utc_from_ns(started.utc_ns))
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": lease._pin.user_agent,
            "OK-ACCESS-KEY": lease._pin.credentials[0],
            "OK-ACCESS-SIGN": build_signature(
                timestamp=signed_at,
                method="GET",
                request_path=request_path,
                body="",
                secret=lease._pin.credentials[1],
            ),
            "OK-ACCESS-PASSPHRASE": lease._pin.credentials[2],
            "OK-ACCESS-TIMESTAMP": signed_at,
            "Accept-Encoding": "identity",
        }
        if (
            type(headers) is not dict
            or set(headers) != _SIGNED_HEADERS
            or any(type(value) is not str for value in headers.values())
            or _SIGNED_TIME.fullmatch(headers.get("OK-ACCESS-TIMESTAMP", "")) is None
            or headers.get("OK-ACCESS-KEY") != lease._pin.credentials[0]
            or headers.get("OK-ACCESS-PASSPHRASE") != lease._pin.credentials[2]
            or headers.get("Content-Type") != "application/json"
            or headers.get("Accept") != "application/json"
            or headers.get("User-Agent") != lease._pin.user_agent
            or headers.get("OK-ACCESS-SIGN")
            != build_signature(
                timestamp=headers.get("OK-ACCESS-TIMESTAMP", ""),
                method="GET",
                request_path=request_path,
                body="",
                secret=lease._pin.credentials[1],
            )
            or "x-simulated-trading" in {key.lower() for key in headers}
            or headers.get("Accept-Encoding") != "identity"
        ):
            _deny("live_config_signed_request_invalid")
        request = http.build_request(
            "GET", lease._pin.origin + request_path, headers=headers, content=None
        )
        if (
            request.method != "GET"
            or str(request.url) != lease._pin.origin + request_path
            or request.content != b""
            or request.headers.get("accept-encoding") != "identity"
            or "x-simulated-trading" in request.headers
        ):
            _deny("live_config_signed_request_invalid")
        signed_request_sha256 = sha(
            canonical(
                {
                    "method": request.method,
                    "url": str(request.url),
                    "signature": request.headers["OK-ACCESS-SIGN"],
                }
            )
        )
        client._final_dispatch_check(method="GET", path=path)
        _check_client(http, synthetic=synthetic)
        lease.verify_current()
        body = bytearray()
        async with asyncio.timeout_at(loop_deadline):
            dispatched = _stamp()
            _validate_clock((started, dispatched))
            _validate_signed_time(headers["OK-ACCESS-TIMESTAMP"], started, dispatched)
            if (
                dispatched.monotonic_ns >= native_deadline_ns
                or loop.time() >= loop_deadline
            ):
                _deny("live_config_request_deadline_exceeded")
            lease.verify_current()
            response = await http.send(
                request, auth=None, follow_redirects=False, stream=True
            )
            headers_received = _stamp()
            lease.verify_current()
            if (
                type(response) is not httpx.Response
                or response.status_code != 200
                or response.request is not request
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
                or "set-cookie" in response.headers
                or http.cookies
            ):
                _deny("live_config_http_response_invalid")
            certificate_hash = _tls_certificate_hash(
                response, http, synthetic=synthetic
            )
            raw_headers = tuple(response.headers.raw)
            length = _checked_response_headers(
                raw_headers,
                private_tokens=(
                    *lease._pin.credentials,
                    request.headers["OK-ACCESS-SIGN"],
                ),
            )
            chunks = 0
            async for chunk in response.aiter_raw():
                lease.verify_current()
                chunks += 1
                if (
                    type(chunk) is not bytes
                    or chunks > MAX_CHUNKS
                    or len(body) + len(chunk) > MAX_BODY
                ):
                    _deny("live_config_body_bound")
                body.extend(chunk)
            body_completed = _stamp()
            lease.verify_current()
            if not body or (length is not None and len(body) != length):
                _deny("live_config_body_incomplete")
        closing = response
        response = None
        await _close_once(closing)
        closed = _stamp()
        _validate_clock((started, dispatched, headers_received, body_completed, closed))
        if closed.monotonic_ns - started.monotonic_ns >= int(
            min(lease._pin.timeout_seconds, MAX_REQUEST_SECONDS) * 1e9
        ):
            _deny("live_config_request_deadline_exceeded")
        lease.verify_current()
        raw = bytes(body)
        signature = request.headers["OK-ACCESS-SIGN"]
        canonical_body = validate_body(raw, lease, signature)
        if signature.encode("ascii") in raw or any(
            token.encode("utf-8") in raw for token in lease._pin.credentials
        ):
            _deny("live_config_credential_echo_rejected")
        validated = _stamp()
        stamps = (
            started,
            dispatched,
            headers_received,
            body_completed,
            closed,
            validated,
        )
        _validate_clock(stamps)
        return _OwnedRawGetV1(
            raw_body=raw,
            raw_headers=raw_headers,
            canonical_body=canonical_body,
            signed_at=headers["OK-ACCESS-TIMESTAMP"],
            stamps=stamps,
            request_signature_sha256=signed_request_sha256,
            tls_certificate_sha256=certificate_hash,
            synthetic_mock=synthetic,
        )
    except asyncio.CancelledError:
        raise
    except LiveConfigObservationError:
        raise
    except _LivePageBodyError:
        raise
    except Exception:  # noqa: BLE001 - redact all HTTP and raw JSON failures
        _deny("live_config_observation_failed")
    finally:
        if response is not None:
            await _close_once(response)
        if own_client and http is not None:
            await _close_once(http)


async def observe_live_account_config_v1(
    client: OkxLivePinnedGetClientV1, lease: LivePinnedGetLeaseV1
) -> LiveAccountConfigObservationV1:
    """Issue exactly one config GET and retain bounded raw bytes before decode."""
    owned = await _observe_owned_live_get_v1(
        client,
        lease,
        path=PATH,
        request_path=PATH,
        validate_body=lambda raw, active, signature: _checked_config(
            raw, lease=active, signature=signature
        ),
    )
    unsealed = LiveAccountConfigObservationV1(
        raw_body=owned.raw_body,
        raw_headers=owned.raw_headers,
        signed_at=owned.signed_at,
        stamps=owned.stamps,
        lease_binding_id=lease.binding_id,
        origin=lease._pin.origin,
        raw_sha256=sha(owned.raw_body),
        canonical_sha256=sha(owned.canonical_body),
        headers_sha256=_header_digest(owned.raw_headers),
        request_signature_sha256=owned.request_signature_sha256,
        expected_uid_sha256=sha(lease._pin.expected_uid.encode()),
        expected_main_uid_sha256=sha(lease._pin.expected_main_uid.encode()),
        tls_certificate_sha256=owned.tls_certificate_sha256,
        receipt_sha256="",
        synthetic_mock=owned.synthetic_mock,
    )
    observation = replace(
        unsealed,
        receipt_sha256=sha(canonical(_receipt_document(unsealed))),
    )
    verify_live_account_config_readback_v1(observation, lease=lease)
    return observation
