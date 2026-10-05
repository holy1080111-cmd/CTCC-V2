"""Owned Demo-only GET capture, never a complete or authenticated risk snapshot.

OKX primary contracts checked 2026-10-05:
https://app.okx.com/docs-v5/en/#overview-rest-authentication
https://app.okx.com/docs-v5/en/#overview-demo-trading-services
https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration
https://app.okx.com/docs-v5/trick_en/#pagination

The credential handle, trusted injected UTC clock and TLS runtime are dependencies,
not attestations. The fixed simulation header cannot authenticate a caller's claim
that a key is a Demo key; both config UID pins must also match. No settings, secret
loading, private SDK, DB, execution or retry integration exists here. Callers must
supply a dedicated Demo read credential through a separately reviewed integration.
Tests replace ONLY the private client factory with synthetic MockTransport.

Legacy 23-stream and v4 38-stream packets remain replayable; current v5 captures
use 34 exact queries. A successful result is still
records_verified_incomplete_account. Rate limiting, server errors
and missing/unsupported coverage abort this attempt; no old mirror or zero fallback
exists. Engineering bounds are inherited from the externally pinned capture plan.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import httpcore
import httpx

from app.trade_qualification import account_capture as capture

BASE_URL = "https://www.okx.com"
CLEANUP_SECONDS = 1
_CREDENTIAL_FIELDS = (
    "api_key",
    "api_secret",
    "passphrase",
    "session_binding_id",
    "environment",
)
_ERROR_CODES = frozenset(
    {
        "credentials_type_invalid",
        "credentials_invalid",
        "demo_credentials_required",
        "credential_session_invalid",
        "credential_session_mismatch",
        "client_invalid",
        "client_not_isolated",
        "transport_not_isolated",
        "verified_tls_required",
        "tls_response_evidence_missing",
        "cleanup_failed",
        "credential_echo_rejected",
        "batch_clock_reversed",
        "batch_deadline_exceeded",
        "publication_barrier_not_crossed",
        "request_clock_reversed",
        "request_deadline_exceeded",
        "http_response_rejected",
        "response_media_type_invalid",
        "response_encoding_rejected",
        "response_bytes_limit",
        "response_length_mismatch",
        "clock_invalid",
        "stream_page_limit",
        "inventory_page_limit",
        "inventory_bytes_limit",
        "inventory_rows_limit",
        "conflicting_algo_identity",
        "account_records_invalid",
        "account_transport_failed",
        "account_capture_invalid",
        "account_plan_version_retired",
    }
)
# Explicit reviewed vocabulary, never derived from response keys, exception text,
# source introspection or an external provider's error message. New capture codes
# intentionally remain unknown until reviewed here.
_CAPTURE_REASONS = frozenset(
    {
        "account_capture_invalid",
        "clock_invalid",
        "plan_history_window_invalid",
        "plan_leverage_scope_invalid",
        "plan_inventory_budget_invalid",
        "source_instrument_type_invalid",
        "source_quantity_unit_invalid",
        "conflicting_history_product_identity",
        "leverage_instrument_scope_mismatch",
        "history_window_requires_milliseconds",
        "account_capture_cannot_grant_authority",
        "record_traversal_limit",
        "record_fields_invalid",
        "record_sequence_limit",
        "record_text_limit",
        "record_bytes_limit",
        "record_decimal_invalid",
        "record_integer_limit",
        "record_scalar_invalid",
        "record_type_invalid",
        "record_invalid",
        "external_plan_pin_invalid",
        "external_plan_pin_mismatch",
        "stream_invalid",
        "cursor_invalid",
        "cursor_not_supported",
        "duplicate_json_key",
        "json_number_invalid",
        "json_traversal_limit",
        "json_object_limit",
        "json_key_limit",
        "secret_field_forbidden",
        "json_array_limit",
        "json_text_limit",
        "json_unicode_invalid",
        "json_scalar_invalid",
        "response_bytes_invalid",
        "packet_bytes_invalid",
        "canonical_bytes_limit",
        "response_json_invalid",
        "packet_json_invalid",
        "source_identity_field_invalid",
        "source_identifier_invalid",
        "source_timestamp_invalid",
        "source_decimal_invalid",
        "source_row_invalid",
        "account_identity_mismatch",
        "account_mode_unsupported",
        "source_inventory_invalid",
        "duplicate_source_identity",
        "history_instrument_scope_mismatch",
        "position_side_invalid",
        "margin_mode_invalid",
        "order_side_invalid",
        "order_state_invalid",
        "algo_query_scope_mismatch",
        "algo_state_invalid",
        "future_source_timestamp",
        "source_lifecycle_reversed",
        "source_quantity_negative",
        "filled_quantity_exceeds_order",
        "history_row_outside_query",
        "page_index_invalid",
        "receipt_pin_invalid",
        "page_chain_metadata_invalid",
        "nonpaginated_page_invalid",
        "identity_receipt_required",
        "request_clock_invalid",
        "request_deadline_exceeded",
        "response_envelope_invalid",
        "response_envelope_fields_invalid",
        "response_gateway_time_invalid",
        "response_row_limit",
        "response_cardinality_invalid",
        "source_cursor_order_invalid",
        "source_cursor_not_exclusive",
        "observation_replay_mismatch",
        "inventory_page_count_invalid",
        "inventory_bytes_limit",
        "inventory_order_invalid",
        "cross_request_clock_reversed",
        "identity_chain_mismatch",
        "batch_deadline_exceeded",
        "inventory_stream_missing_or_limit",
        "nonpaginated_inventory_duplicate",
        "empty_terminal_page_required",
        "page_index_gap",
        "page_after_terminal",
        "page_chain_mismatch",
        "duplicate_or_conflicting_page_identity",
        "conflicting_algo_type_identity",
        "account_mode_changed",
        "inventory_rows_limit",
        "packet_replay_mismatch",
        "packet_bytes_limit",
        "external_packet_pin_invalid",
        "external_packet_pin_mismatch",
        "packet_noncanonical",
        "packet_schema_invalid",
    }
)
_DIAGNOSTIC_STAGES = frozenset({"parse_observation", "verify_records", "freeze_packet"})


@dataclass(frozen=True, slots=True)
class AccountCollectionDiagnostic:
    """Only bounded local metadata; never response details or authority evidence."""

    stage: Literal["parse_observation", "verify_records", "freeze_packet"]
    stream: capture.Stream | None
    page_index: int | None
    capture_reason: str

    def __post_init__(self):
        if (
            type(self.stage) is not str
            or not 1 <= len(self.stage) <= 20
            or self.stage not in _DIAGNOSTIC_STAGES
            or type(self.capture_reason) is not str
            or not 1 <= len(self.capture_reason) <= 96
            or self.capture_reason not in _CAPTURE_REASONS | {"unknown"}
        ):
            raise ValueError("diagnostic_invalid")
        if self.stage == "parse_observation":
            if (
                type(self.stream) is not str
                or not 1 <= len(self.stream) <= 32
                or self.stream not in capture.ALL_STREAMS
                or type(self.page_index) is not int
                or not 0 <= self.page_index < 64
            ):
                raise ValueError("diagnostic_invalid")
        elif self.stream is not None or self.page_index is not None:
            raise ValueError("diagnostic_invalid")


class AccountCollectionError(ValueError):
    """Fixed local codes only; never carry HTTP messages, headers or raw bodies."""

    __slots__ = ("_diagnostic",)

    def __init__(self, *args, diagnostic: AccountCollectionDiagnostic | None = None):
        if (
            diagnostic is not None
            and type(diagnostic) is not AccountCollectionDiagnostic
        ):
            raise ValueError("diagnostic_invalid")
        super().__init__(*args)
        self._diagnostic = diagnostic

    @property
    def diagnostic(self) -> AccountCollectionDiagnostic | None:
        return self._diagnostic


def _capture_diagnostic(error, *, stage, stream, page_index, secret_tokens):
    # Called only inside the three actual capture API exception boundaries. The
    # caller excludes subclasses first, so accessing native exception slots cannot
    # invoke a foreign __getattribute__, __str__, __eq__ or __hash__ callback.
    reason = "unknown"
    arguments = object.__getattribute__(error, "args")
    fields = object.__getattribute__(error, "__dict__")
    if (
        type(fields) is dict
        and not fields
        and type(arguments) is tuple
        and len(arguments) == 1
        and type(arguments[0]) is str
        and 1 <= len(arguments[0]) <= 96
        and arguments[0] in _CAPTURE_REASONS
        and not any(token in arguments[0] for token in secret_tokens)
    ):
        reason = arguments[0]
    return AccountCollectionDiagnostic(
        stage=stage, stream=stream, page_index=page_index, capture_reason=reason
    )


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class DemoAccountCredentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    passphrase: str = field(repr=False)
    session_binding_id: str
    environment: str = "demo"

    def __post_init__(self):
        _credential_values(self)

    def __repr__(self):
        return "<DemoAccountCredentials redacted>"


def _credential_values(value):
    if type(value) is not DemoAccountCredentials:
        raise AccountCollectionError("credentials_type_invalid")
    try:
        parts = tuple(
            object.__getattribute__(value, name) for name in _CREDENTIAL_FIELDS
        )
    except AttributeError:
        raise AccountCollectionError("credentials_invalid") from None
    if any(type(part) is not str for part in parts):
        raise AccountCollectionError("credentials_invalid")
    key, secret, passphrase, session, environment = parts
    # Bounds are a local input-safety contract, not proof of provider permissions.
    if any(
        not 8 <= len(part) <= 256
        or not part.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in part)
        for part in (key, secret, passphrase)
    ):
        raise AccountCollectionError("credentials_invalid")
    if environment != "demo":
        raise AccountCollectionError("demo_credentials_required")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", session) is None:
        raise AccountCollectionError("credential_session_invalid")
    return parts


def _new_client():
    """Private test seam; external/shared clients or transports are not API inputs."""
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
        follow_redirects=False,
        auth=None,
    )


def _client_guard(client, *, first):
    if type(client) is not httpx.AsyncClient or client.is_closed:
        raise AccountCollectionError("client_invalid")
    if (
        client.trust_env
        or client.auth is not None
        or client.follow_redirects
        or client.params
        or (first and client.cookies)
        or any(client.event_hooks.values())
        or any(
            name not in {"accept", "accept-encoding", "connection", "user-agent"}
            for name in client.headers
        )
        or type(client._mounts) is not dict
        or any(item is not None for item in client._mounts.values())
    ):
        raise AccountCollectionError("client_not_isolated")
    transport = client._transport
    if type(transport) is httpx.MockTransport:
        return  # Explicit synthetic test dependency; never source authentication.
    if type(transport) is not httpx.AsyncHTTPTransport:
        raise AccountCollectionError("transport_not_isolated")
    pool = transport._pool
    if (
        type(pool) is not httpcore.AsyncConnectionPool
        or type(pool._retries) is not int
        or pool._retries != 0
    ):
        raise AccountCollectionError("transport_not_isolated")
    context = pool._ssl_context
    if (
        type(context) is not ssl.SSLContext
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise AccountCollectionError("verified_tls_required")


async def _close(resource, *, pending_cancel=False):
    """One shielded bounded close; cancellation survives failed cleanup."""

    async def once():
        async with asyncio.timeout(CLEANUP_SECONDS):
            await resource.aclose()

    task = asyncio.create_task(once())
    interrupted = pending_cancel
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:  # noqa: BLE001 -- cleanup cannot expose transport details
            break
    failed = False
    try:
        task.result()
    except asyncio.CancelledError:
        failed = True
    except Exception:  # noqa: BLE001 -- cleanup cannot expose transport details
        failed = True
    if interrupted:
        raise asyncio.CancelledError
    if failed:
        raise AccountCollectionError("cleanup_failed")


def _no_secrets(payload, tokens):
    if any(token.encode("ascii") in payload for token in tokens):
        raise AccountCollectionError("credential_echo_rejected")


def _no_secret_json(canonical, tokens):
    # Only already bounded/replayed JSON reaches this traversal. Checking decoded
    # strings also catches escaped quote/backslash/unicode forms of a credential.
    pending = [json.loads(canonical)]
    while pending:
        item = pending.pop()
        if type(item) is str:
            if any(token in item for token in tokens):
                raise AccountCollectionError("credential_echo_rejected")
        elif type(item) is dict:
            pending.extend(item)
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)


def _read_clock(clock):
    return capture._utc(clock())


def _phase_clock(clock, observer, *, phase, request_index, stream, page_index):
    if observer is None:
        return _read_clock(clock)
    from app.trade_qualification.account_native_clock import _phase

    return _phase(
        observer,
        clock,
        phase=phase,
        request_index=request_index,
        stream=stream,
        page_index=page_index,
    )


def _check_batch(now, started, previous, seconds):
    if now < previous:
        raise AccountCollectionError("batch_clock_reversed")
    if now - started > timedelta(seconds=seconds):
        raise AccountCollectionError("batch_deadline_exceeded")


def _signed_request(spec, credentials, started, timeout):
    # spec is generated from the already copied/pinned plan, not caller metadata.
    url = httpx.URL(spec.origin + spec.endpoint, params=spec.parameters or None)
    timestamp = started.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    prehash = timestamp.encode("ascii") + b"GET" + url.raw_path
    signature = base64.b64encode(
        hmac.new(
            credentials.api_secret.encode("ascii"), prehash, hashlib.sha256
        ).digest()
    ).decode("ascii")
    request = httpx.Request(
        "GET",
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "User-Agent": "CTCC-demo-account-records/1",
            "OK-ACCESS-KEY": credentials.api_key,
            "OK-ACCESS-PASSPHRASE": credentials.passphrase,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-SIGN": signature,
            "x-simulated-trading": "1",
        },
        extensions={"timeout": httpx.Timeout(timeout).as_dict()},
    )
    return request, signature


async def _page(
    client,
    credentials,
    clock,
    plan,
    pin,
    barrier,
    *,
    stream,
    page_index,
    after,
    previous_sha,
    identity,
    previous_time,
    batch_started,
    remaining_bytes,
    secret_tokens,
    wall_deadline,
    capture_proofs,
    _journal=None,
    _native_observer=None,
    _request_index=None,
):
    _client_guard(client, first=False)
    spec = capture.account_request(plan, stream, after)
    loop = asyncio.get_running_loop()
    request_wall_deadline = loop.time() + plan.max_request_seconds
    started = _phase_clock(
        clock,
        _native_observer,
        phase="request_start",
        request_index=_request_index,
        stream=stream,
        page_index=page_index,
    )
    _check_batch(started, batch_started, previous_time, plan.max_batch_seconds)
    if started <= barrier or started < plan.created_at:
        raise AccountCollectionError("publication_barrier_not_crossed")
    request, signature = _signed_request(
        spec, credentials, started, plan.max_request_seconds
    )
    secret_tokens.append(signature)
    if _journal is not None:
        await _journal.request(
            stream=stream,
            page_index=page_index,
            after=after,
            previous_sha=previous_sha,
            identity=identity,
            spec=spec,
            started=started,
        )
    maximum = min(plan.max_response_bytes, remaining_bytes)
    body = bytearray()
    async with asyncio.timeout_at(min(wall_deadline, request_wall_deadline)):
        # A synchronous clock/signing delay can exhaust a timeout before the loop
        # yields to deliver cancellation. Do not issue even one GET after that.
        if loop.time() >= wall_deadline:
            raise AccountCollectionError("batch_deadline_exceeded")
        if loop.time() >= request_wall_deadline:
            raise AccountCollectionError("request_deadline_exceeded")
        if _native_observer is not None:
            _phase_clock(
                clock,
                _native_observer,
                phase="request_dispatch",
                request_index=_request_index,
                stream=stream,
                page_index=page_index,
            )
        response = await client.send(
            request, auth=None, follow_redirects=False, stream=True
        )
        pending_cancel = False
        try:
            try:
                received = _phase_clock(
                    clock,
                    _native_observer,
                    phase="headers_received",
                    request_index=_request_index,
                    stream=stream,
                    page_index=page_index,
                )
            except Exception:
                if _journal is not None and type(response) is httpx.Response:
                    await _journal.headers(None, response.status_code)
                raise
            if _journal is not None and type(response) is httpx.Response:
                await _journal.headers(received, response.status_code)
            if received < started:
                raise AccountCollectionError("request_clock_reversed")
            if (
                type(response) is not httpx.Response
                or response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
            ):
                raise AccountCollectionError("http_response_rejected")
            # Runtime source provenance comes from this owned response, never a
            # DTO's passed/complete flag. MockTransport can only record synthetic.
            if type(client._transport) is httpx.MockTransport:
                capture_proofs.append(None)
            else:
                network = response.extensions.get("network_stream")
                tls = None if network is None else network.get_extra_info("ssl_object")
                if (
                    type(tls) is not ssl.SSLObject
                    or tls.context is not client._transport._pool._ssl_context
                    or tls.server_hostname != request.url.host
                    or tls.version() not in {"TLSv1.2", "TLSv1.3"}
                ):
                    raise AccountCollectionError("tls_response_evidence_missing")
                certificate = tls.getpeercert(binary_form=True)
                if type(certificate) is not bytes or not certificate:
                    raise AccountCollectionError("tls_response_evidence_missing")
                capture_proofs.append(hashlib.sha256(certificate).hexdigest())
            if _journal is not None:
                _journal.transport(capture_proofs[-1])
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
            ):
                raise AccountCollectionError("response_media_type_invalid")
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise AccountCollectionError("response_encoding_rejected")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 10
                or int(length) > maximum
            ):
                raise AccountCollectionError("response_bytes_limit")
            # Direct stream reading avoids HTTPX's automatic EOF close race.
            async for chunk in response.stream:
                if _journal is not None:
                    try:
                        chunk_received = _read_clock(clock)
                    except Exception:
                        await _journal.chunk(chunk, None)
                        raise
                    await _journal.chunk(chunk, chunk_received)
                if type(chunk) is not bytes or len(body) + len(chunk) > maximum:
                    raise AccountCollectionError("response_bytes_limit")
                body.extend(chunk)
            if _journal is not None:
                try:
                    body_exhausted = _phase_clock(
                        clock,
                        _native_observer,
                        phase="body_exhausted",
                        request_index=_request_index,
                        stream=stream,
                        page_index=page_index,
                    )
                except Exception:
                    await _journal.body_complete(None)
                    raise
                await _journal.body_complete(body_exhausted)
        except asyncio.CancelledError:
            pending_cancel = True
            raise
        finally:
            await _close(response, pending_cancel=pending_cancel)
    completed = _phase_clock(
        clock,
        _native_observer,
        phase="response_closed",
        request_index=_request_index,
        stream=stream,
        page_index=page_index,
    )
    _check_batch(completed, batch_started, received, plan.max_batch_seconds)
    if length is not None and len(body) != int(length):
        raise AccountCollectionError("response_length_mismatch")
    raw = bytes(body)
    _no_secrets(raw, secret_tokens)
    try:
        observation = capture.parse_demo_account_observation(
            raw,
            plan=plan,
            expected_plan_sha256=pin,
            stream=stream,
            request_started_at=started,
            headers_received_at=received,
            body_completed_at=completed,
            barrier_completed_at=barrier,
            page_index=page_index,
            after=after,
            previous_page_sha256=previous_sha,
            identity_receipt_sha256=identity,
        )
    except capture.AccountCaptureError as exc:
        if type(exc) is not capture.AccountCaptureError:
            raise
        # A diagnostic is an internal rejection result, never a partial receipt.
        return _capture_diagnostic(
            exc,
            stage="parse_observation",
            stream=stream,
            page_index=page_index,
            secret_tokens=secret_tokens,
        )
    _no_secret_json(observation.canonical_json, secret_tokens)
    if _journal is not None:
        await _journal.validated(observation)
    return observation


_CAPTURE_ISSUER = object()


@dataclass(frozen=True, slots=True, repr=False, init=False)
class _OwnedAccountCapture:
    """Process-local result of actual owned IO; serialized packets cannot mint it.

    This is an internal provenance carrier, not an order capability, portable TLS
    attestation, bootstrap verification or complete portfolio snapshot.
    """

    packet: capture.DemoAccountPacket
    completed_at: datetime
    peer_certificate_sha256: tuple[str | None, ...]

    def __init__(self, issuer, packet, completed_at, peer_certificate_sha256):
        if issuer is not _CAPTURE_ISSUER:
            raise AccountCollectionError("account_capture_invalid")
        object.__setattr__(self, "packet", packet)
        object.__setattr__(self, "completed_at", completed_at)
        object.__setattr__(self, "peer_certificate_sha256", peer_certificate_sha256)

    def __repr__(self):
        return "<OwnedAccountCapture private>"


async def collect_demo_account_records(
    *,
    credentials: DemoAccountCredentials,
    clock: Callable[[], datetime],
    plan: capture.DemoAccountCapturePlan,
    expected_plan_sha256: str,
    barrier_completed_at: datetime,
) -> capture.DemoAccountPacket:
    """Portable audit DTO; never carries process-local transport provenance."""
    result = await _collect_owned_demo_account_records(
        credentials=credentials,
        clock=clock,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
        barrier_completed_at=barrier_completed_at,
    )
    return result.packet


async def _collect_owned_demo_account_records(
    *,
    credentials: DemoAccountCredentials,
    clock: Callable[[], datetime],
    plan: capture.DemoAccountCapturePlan,
    expected_plan_sha256: str,
    barrier_completed_at: datetime,
    _journal=None,
    _native_observer=None,
) -> _OwnedAccountCapture:
    """One owned client, fixed GET inventory, full replay, no partial packet.

    No external client/signer/URL can be supplied. Per-page clocks preserve source
    update semantics; auth timestamps never replace source timestamps. Every page
    starts strictly after the mandatory publication barrier. Cleanup must finish
    before returning; its clock is checked but does not overwrite raw receipt time.
    """
    error = None
    diagnostic = None
    interrupted = False
    owned_result = None
    active_journal = None
    try:
        if type(plan) not in {
            capture.DemoAccountCapturePlan,
            capture.RegionalDemoAccountCapturePlan,
            capture.AllProductDemoAccountCapturePlan,
            capture.CurrentDemoAccountCapturePlanV6,
        }:
            raise AccountCollectionError("account_plan_version_retired")
        selected = capture._checked_plan(plan, expected_plan_sha256)
        values = _credential_values(credentials)
        credentials = DemoAccountCredentials(*values)  # private immutable snapshot
        if credentials.session_binding_id != selected.session_binding_id:
            raise AccountCollectionError("credential_session_mismatch")
        secret_tokens = [
            credentials.api_key,
            credentials.api_secret,
            credentials.passphrase,
        ]
        if _journal is not None:
            from app.trade_qualification.account_capture_journal import (
                _OwnedAccountJournal,
            )

            if type(_journal) is not _OwnedAccountJournal or _journal.closed:
                raise AccountCollectionError("account_capture_invalid")
            if capture.plan_sha256(_journal.plan) != expected_plan_sha256:
                raise AccountCollectionError("account_capture_invalid")
            active_journal = _journal
            active_journal.bind_tokens(secret_tokens)
        if _native_observer is not None:
            from app.trade_qualification.account_native_clock import _bind_collector

            _bind_collector(
                _native_observer, clock, expected_plan_sha256, active_journal
            )
        _no_secret_json(
            capture._canonical(capture._json_value(selected)),
            secret_tokens,
        )
        barrier = capture._utc(barrier_completed_at)
        if not callable(clock):
            raise AccountCollectionError("clock_invalid")
        batch_started = _read_clock(clock)
        if batch_started <= barrier or batch_started < selected.created_at:
            raise AccountCollectionError("publication_barrier_not_crossed")
        loop = asyncio.get_running_loop()
        wall_started = loop.time()
        client = _new_client()
        # An invalid foreign factory result must not execute its cleanup callback.
        if type(client) is not httpx.AsyncClient:
            raise AccountCollectionError("client_invalid")
        cancelled = False
        observations = []
        capture_proofs = []
        rows = total_bytes = 0
        identity = None
        previous_time = batch_started
        algo_ids = set()
        try:
            _client_guard(client, first=True)
            # Legacy plan contracts still support synthetic historical-parser
            # fixtures, but their request inventory contains undocumented algo
            # ordTypes. Only v5 may reach the owned HTTPS transport.
            if (
                type(selected)
                not in {
                    capture.AllProductDemoAccountCapturePlan,
                    capture.CurrentDemoAccountCapturePlanV6,
                }
                and type(client._transport) is not httpx.MockTransport
            ):
                raise AccountCollectionError("account_plan_version_retired")
            if loop.time() - wall_started >= selected.max_batch_seconds:
                raise AccountCollectionError("batch_deadline_exceeded")
            async with asyncio.timeout_at(wall_started + selected.max_batch_seconds):
                for stream in capture.streams_for_plan(selected):
                    after = previous_sha = None
                    page_index = 0
                    while True:
                        if page_index >= selected.max_pages_per_stream:
                            raise AccountCollectionError("stream_page_limit")
                        if len(observations) >= selected.max_total_pages:
                            raise AccountCollectionError("inventory_page_limit")
                        if total_bytes >= selected.max_total_bytes:
                            raise AccountCollectionError("inventory_bytes_limit")
                        item = await _page(
                            client,
                            credentials,
                            clock,
                            selected,
                            expected_plan_sha256,
                            barrier,
                            stream=stream,
                            page_index=page_index,
                            after=after,
                            previous_sha=previous_sha,
                            identity=identity,
                            previous_time=previous_time,
                            batch_started=batch_started,
                            remaining_bytes=selected.max_total_bytes - total_bytes,
                            secret_tokens=secret_tokens,
                            wall_deadline=wall_started + selected.max_batch_seconds,
                            capture_proofs=capture_proofs,
                            _journal=active_journal,
                            **(
                                {}
                                if _native_observer is None
                                else {
                                    "_native_observer": _native_observer,
                                    "_request_index": len(observations),
                                }
                            ),
                        )
                        if type(item) is AccountCollectionDiagnostic:
                            diagnostic = item
                            raise AccountCollectionError("account_records_invalid")
                        rows += len(item.rows)
                        total_bytes += item.body_size_bytes
                        if rows > selected.max_total_rows:
                            raise AccountCollectionError("inventory_rows_limit")
                        if stream.startswith("algo_"):
                            for row in item.rows:
                                if row.row_id in algo_ids:
                                    raise AccountCollectionError(
                                        "conflicting_algo_identity"
                                    )
                                algo_ids.add(row.row_id)
                        observations.append(item)
                        previous_time = item.body_completed_at
                        if stream == "config_before":
                            identity = item.receipt_sha256
                        if item.terminal:
                            break
                        after, previous_sha = item.rows[-1].row_id, item.receipt_sha256
                        page_index += 1
                # Include signatures from later requests too: no issued secret may
                # be hidden in an earlier retained page under an innocuous key.
                for item in observations:
                    _no_secret_json(item.canonical_json, secret_tokens)
                try:
                    packet = capture.verify_demo_account_records(
                        tuple(observations),
                        plan=selected,
                        expected_plan_sha256=expected_plan_sha256,
                        barrier_completed_at=barrier,
                    )
                except capture.AccountCaptureError as exc:
                    if type(exc) is capture.AccountCaptureError:
                        diagnostic = _capture_diagnostic(
                            exc,
                            stage="verify_records",
                            stream=None,
                            page_index=None,
                            secret_tokens=secret_tokens,
                        )
                    raise
                # The retained raw/canonical/derived views can expand beyond the
                # raw-byte budget. Require the existing canonical artifact bound,
                # too; do not return a packet that cannot be frozen and replayed.
                try:
                    capture.freeze_demo_account_packet(
                        packet,
                        expected_plan_sha256=expected_plan_sha256,
                    )
                except capture.AccountCaptureError as exc:
                    if type(exc) is capture.AccountCaptureError:
                        diagnostic = _capture_diagnostic(
                            exc,
                            stage="freeze_packet",
                            stream=None,
                            page_index=None,
                            secret_tokens=secret_tokens,
                        )
                    raise
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            await _close(client, pending_cancel=cancelled)
        finished = _phase_clock(
            clock,
            _native_observer,
            phase="source_closed",
            request_index=len(observations),
            stream="account_source",
            page_index=0,
        )
        _check_batch(finished, batch_started, previous_time, selected.max_batch_seconds)
        if loop.time() - wall_started > selected.max_batch_seconds:
            raise AccountCollectionError("batch_deadline_exceeded")
        owned_result = _OwnedAccountCapture(
            _CAPTURE_ISSUER, packet, finished, tuple(capture_proofs)
        )
    except asyncio.CancelledError:
        interrupted = True
    except AccountCollectionError as exc:
        # Only this module's fixed code; never a stringified external exception.
        error = "account_capture_invalid"
        if (
            type(exc) is AccountCollectionError
            and type(exc.args) is tuple
            and len(exc.args) == 1
            and type(exc.args[0]) is str
            and exc.args[0] in _ERROR_CODES
        ):
            error = exc.args[0]
    except capture.AccountCaptureError:
        error = "account_records_invalid"
    except (httpx.HTTPError, TimeoutError):
        error = "account_transport_failed"
    except Exception:  # noqa: BLE001 -- never expose secret-bearing transport errors
        error = "account_capture_invalid"
    if active_journal is not None:
        from app.trade_qualification.account_capture_journal import bounded_finalization

        if owned_result is not None:
            try:
                active_journal.bind_capture(owned_result)
            except Exception:  # noqa: BLE001 -- finalize raw even after binding failure
                owned_result, error = None, "account_capture_invalid"
        try:
            closure = active_journal.close_acquisition(
                successful=owned_result is not None
            )
            if _native_observer is not None:
                from app.trade_qualification.account_native_clock import (
                    _finalization_awaitable,
                )

                closure = _finalization_awaitable(
                    _native_observer, active_journal, closure, purpose="source"
                )
            await bounded_finalization(closure)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:  # noqa: BLE001 -- private persistence errors cannot escape
            owned_result, error = None, "account_capture_invalid"
    # Outside except: no implicit HTTP exception chain containing auth headers.
    if interrupted:
        raise asyncio.CancelledError
    if owned_result is not None:
        return owned_result
    raise AccountCollectionError(
        error, diagnostic=diagnostic if error == "account_records_invalid" else None
    )
