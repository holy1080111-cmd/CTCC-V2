"""Owned, signed Demo position-history acquisition; permanently DENY-only.

The retained archive is a source-bound diagnostic, not a portable TLS
attestation or a complete account/history certificate. No order writer imports
this module. Tests substitute only the private client factory.
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
from datetime import UTC, datetime

import httpx

from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as owned
from app.trade_qualification import account_positions_history_v1 as history

VERSION = "ctcc.demo_positions_history_owned.v1"
CONFIG_ENDPOINT = "/api/v5/account/config"
MAX_CONFIG_BYTES = 65536
MAX_ARCHIVE_BYTES = history.MAX_ARCHIVE_BYTES + 2 * MAX_CONFIG_BYTES + 32768
MAX_BATCH_SECONDS = 240
_IDENTIFIER = re.compile(r"[1-9][0-9]{0,39}\Z")
_CURRENCY = re.compile(r"[A-Z]{2,10}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class OwnedPositionHistoryError(ValueError):
    """Fixed local error codes; private bodies and exception text never escape."""


def _deny(code: str):
    raise OwnedPositionHistoryError(code) from None


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("clock_invalid")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class DemoPositionIdentityV1:
    uid: str
    main_uid: str
    account_level: str
    position_mode: str
    settlement_currency: str

    def __post_init__(self):
        if (
            type(self.uid) is not str
            or _IDENTIFIER.fullmatch(self.uid) is None
            or type(self.main_uid) is not str
            or _IDENTIFIER.fullmatch(self.main_uid) is None
            or self.account_level not in {"1", "2", "3", "4"}
            or self.position_mode not in {"net_mode", "long_short_mode"}
            or type(self.settlement_currency) is not str
            or _CURRENCY.fullmatch(self.settlement_currency) is None
        ):
            _deny("identity_pin_invalid")

    @property
    def sha256(self) -> str:
        self.__post_init__()
        return _hash(
            _canonical(
                {
                    "environment": "demo",
                    "uid": self.uid,
                    "mainUid": self.main_uid,
                    "acctLv": self.account_level,
                    "posMode": self.position_mode,
                    "settleCcy": self.settlement_currency,
                }
            )
        )


@dataclass(frozen=True, slots=True, repr=False)
class _RawPage:
    raw: bytes = field(repr=False)
    started: datetime
    headers: datetime
    completed: datetime
    certificate_sha256: str | None


@dataclass(frozen=True, slots=True, repr=False)
class PositionHistoryOwnedArchiveV1:
    raw_archive: bytes = field(repr=False)
    archive_sha256: str
    chain: history.PositionHistoryChainV1
    peer_certificate_sha256: tuple[str | None, ...]

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        # A self-contained archive can be forged and rehashed. The original
        # in-process transport witness cannot be recreated after restart.
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


@dataclass(frozen=True, slots=True)
class _GetSpec:
    origin: str
    endpoint: str
    parameters: tuple[tuple[str, str], ...]


def _config_page(
    raw: _RawPage, plan: history.PositionHistoryPlanV1, identity: DemoPositionIdentityV1
) -> str:
    started, headers, completed = map(_utc, (raw.started, raw.headers, raw.completed))
    if not plan.created_at < started <= headers <= completed:
        _deny("config_clock_invalid")
    if (completed - started).total_seconds() > 15:
        _deny("config_clock_invalid")
    try:
        value, _ = capture._decode_json(raw.raw, limit=MAX_CONFIG_BYTES, wire=True)
        if (
            type(value) is not dict
            or set(value) - {"code", "msg", "data", "inTime", "outTime"}
            or value.get("code") != "0"
            or type(value.get("msg")) is not str
            or type(value.get("data")) is not list
            or len(value["data"]) != 1
            or type(value["data"][0]) is not dict
        ):
            _deny("config_invalid")
        if ("inTime" in value) != ("outTime" in value):
            _deny("config_gateway_clock_invalid")
        if "inTime" in value:
            start = capture._gateway_time(value["inTime"])
            end = capture._gateway_time(value["outTime"])
            if not started <= start <= end <= headers:
                _deny("config_gateway_clock_invalid")
        row = value["data"][0]
        if (
            row.get("uid") != identity.uid
            or row.get("mainUid") != identity.main_uid
            or row.get("acctLv") != identity.account_level
            or row.get("posMode") != identity.position_mode
            or row.get("settleCcy") != identity.settlement_currency
            or any(
                type(row.get(key)) is not str
                for key in ("uid", "mainUid", "acctLv", "posMode", "settleCcy")
            )
        ):
            _deny("account_identity_mismatch")
        return _hash(_canonical(row))
    except capture.AccountCaptureError:
        _deny("config_invalid")


def _record(raw: _RawPage) -> dict[str, str | None]:
    return {
        "raw_body_base64": base64.b64encode(raw.raw).decode("ascii"),
        "request_started_at": raw.started.isoformat(),
        "headers_received_at": raw.headers.isoformat(),
        "body_completed_at": raw.completed.isoformat(),
        "certificate_sha256": raw.certificate_sha256,
    }


def _read_record(record: object) -> _RawPage:
    if type(record) is not dict or set(record) != {
        "raw_body_base64",
        "request_started_at",
        "headers_received_at",
        "body_completed_at",
        "certificate_sha256",
    }:
        _deny("archive_invalid")
    if any(
        type(record[key]) is not str
        for key in (
            "raw_body_base64",
            "request_started_at",
            "headers_received_at",
            "body_completed_at",
        )
    ):
        _deny("archive_invalid")
    certificate = record["certificate_sha256"]
    if certificate is not None and (
        type(certificate) is not str or _SHA.fullmatch(certificate) is None
    ):
        _deny("archive_invalid")
    return _RawPage(
        raw=base64.b64decode(record["raw_body_base64"], validate=True),
        started=datetime.fromisoformat(record["request_started_at"]),
        headers=datetime.fromisoformat(record["headers_received_at"]),
        completed=datetime.fromisoformat(record["body_completed_at"]),
        certificate_sha256=certificate,
    )


def freeze_source_bound_archive(
    plan: history.PositionHistoryPlanV1,
    identity: DemoPositionIdentityV1,
    before: _RawPage,
    pages: tuple[history.PositionHistoryPageV1, ...],
    after: _RawPage,
    certificates: tuple[str | None, ...],
) -> bytes:
    """Retain raw signed-GET response bodies; hashes alone do not attest TLS."""
    if (
        type(plan) is not history.PositionHistoryPlanV1
        or type(identity) is not DemoPositionIdentityV1
        or type(before) is not _RawPage
        or type(after) is not _RawPage
        or type(pages) is not tuple
        or any(type(page) is not history.PositionHistoryPageV1 for page in pages)
    ):
        _deny("binding_invalid")
    plan.__post_init__()
    if (
        identity.sha256 != plan.account_binding_sha256
        or type(certificates) is not tuple
        or len(certificates) != len(pages)
    ):
        _deny("binding_invalid")
    first_config = _config_page(before, plan, identity)
    second_config = _config_page(after, plan, identity)
    if first_config != second_config:
        _deny("config_changed")
    if (
        not pages
        or not before.completed <= pages[0].request_started_at
        or not pages[-1].body_completed_at <= after.started
    ):
        _deny("source_order_invalid")
    chain_bytes = history.freeze_chain(plan, pages)
    payload = _canonical(
        {
            "version": VERSION,
            "plan_sha256": plan.sha256,
            "identity_sha256": identity.sha256,
            "session_binding_sha256": plan.session_binding_sha256,
            "config_before": _record(before),
            "history_archive": json.loads(chain_bytes),
            "history_archive_sha256": _hash(chain_bytes),
            "page_certificate_sha256": certificates,
            "config_after": _record(after),
        }
    )
    if len(payload) > MAX_ARCHIVE_BYTES:
        _deny("archive_byte_limit")
    return payload


def verify_source_bound_archive(
    plan: history.PositionHistoryPlanV1,
    identity: DemoPositionIdentityV1,
    archive: bytes,
    *,
    expected_sha256: str,
) -> PositionHistoryOwnedArchiveV1:
    """Replay a pinned archive; even a matching archive remains DENY-only."""
    try:
        if (
            type(plan) is not history.PositionHistoryPlanV1
            or type(identity) is not DemoPositionIdentityV1
        ):
            _deny("binding_invalid")
        plan.__post_init__()
        if identity.sha256 != plan.account_binding_sha256:
            _deny("binding_invalid")
        if type(expected_sha256) is not str or _SHA.fullmatch(expected_sha256) is None:
            _deny("archive_pin_invalid")
        if type(archive) is not bytes or not 1 <= len(archive) <= MAX_ARCHIVE_BYTES:
            _deny("archive_byte_limit")
        if _hash(archive) != expected_sha256:
            _deny("archive_pin_mismatch")
        value, canonical = capture._decode_json(
            archive, limit=MAX_ARCHIVE_BYTES, wire=False
        )
        if (
            canonical.encode("utf-8") != archive
            or type(value) is not dict
            or set(value)
            != {
                "version",
                "plan_sha256",
                "identity_sha256",
                "session_binding_sha256",
                "config_before",
                "config_after",
                "history_archive",
                "history_archive_sha256",
                "page_certificate_sha256",
            }
        ):
            _deny("archive_invalid")
        if (
            value["version"] != VERSION
            or value["plan_sha256"] != plan.sha256
            or value["identity_sha256"] != identity.sha256
            or value["session_binding_sha256"] != plan.session_binding_sha256
        ):
            _deny("archive_binding_mismatch")
        before, after = (
            _read_record(value["config_before"]),
            _read_record(value["config_after"]),
        )
        if _config_page(before, plan, identity) != _config_page(after, plan, identity):
            _deny("config_changed")
        if type(value["history_archive"]) is not dict:
            _deny("archive_invalid")
        chain_bytes = _canonical(value["history_archive"])
        chain = history.verify_archived_chain(
            plan, chain_bytes, expected_sha256=value["history_archive_sha256"]
        )
        record = json.loads(chain_bytes)
        first_start = _utc(
            datetime.fromisoformat(record["pages"][0]["request_started_at"])
        )
        last_complete = _utc(
            datetime.fromisoformat(record["pages"][-1]["body_completed_at"])
        )
        if not before.completed <= first_start or not last_complete <= after.started:
            _deny("source_order_invalid")
        certs = value["page_certificate_sha256"]
        if (
            type(certs) is not list
            or len(certs) != len(chain.page_hashes)
            or any(
                cert is not None
                and (type(cert) is not str or _SHA.fullmatch(cert) is None)
                for cert in certs
            )
        ):
            _deny("archive_invalid")
        if (
            freeze_source_bound_archive(
                plan,
                identity,
                before,
                _unfreeze_pages(plan, record),
                after,
                tuple(certs),
            )
            != archive
        ):
            _deny("archive_invalid")
        return PositionHistoryOwnedArchiveV1(
            archive, expected_sha256, chain, tuple(certs)
        )
    except OwnedPositionHistoryError:
        raise
    except (
        capture.AccountCaptureError,
        history.PositionHistoryError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
    ):
        _deny("archive_invalid")


def _unfreeze_pages(
    plan: history.PositionHistoryPlanV1, archive_record: dict
) -> tuple[history.PositionHistoryPageV1, ...]:
    pages = []
    for index, item in enumerate(archive_record["pages"]):
        previous = pages[-1] if pages else None
        pages.append(
            history.parse_page(
                plan,
                base64.b64decode(item["raw_body_base64"], validate=True),
                page_index=index,
                after=None if previous is None else previous.next_after,
                previous_page_sha256=None if previous is None else previous.page_sha256,
                request_started_at=datetime.fromisoformat(item["request_started_at"]),
                headers_received_at=datetime.fromisoformat(item["headers_received_at"]),
                body_completed_at=datetime.fromisoformat(item["body_completed_at"]),
            )
        )
    return tuple(pages)


_new_client = owned._new_client


def _checked_read_session(
    session: DemoPinnedReadSession,
    credentials: owned.DemoAccountCredentials,
    *,
    origin: str,
    session_binding_sha256: str,
) -> None:
    """Bind each signature to the active runtime-issued Demo credential lease."""
    if (
        type(session) is not DemoPinnedReadSession
        or type(session._owner) is not OkxDemoPrivateRestClient
    ):
        _deny("credential_session_witness_required")
    try:
        session.verify_current()
    except OkxPrivateApiError:
        _deny("credential_session_inactive")
    key, secret, passphrase, label, _ = owned._credential_values(credentials)
    if (
        session._origin != origin
        or label != session.binding_id
        or _hash(session.binding_id.encode("ascii")) != session_binding_sha256
    ):
        _deny("credential_session_binding_mismatch")
    if not all(
        hmac.compare_digest(actual, expected)
        for actual, expected in zip(
            (key, secret, passphrase), session._credentials, strict=True
        )
    ):
        _deny("credential_session_credentials_mismatch")


async def _signed_get(
    client: httpx.AsyncClient,
    credentials: owned.DemoAccountCredentials,
    read_session: DemoPinnedReadSession,
    session_binding_sha256: str,
    clock: Callable[[], datetime],
    spec: _GetSpec,
    *,
    previous_completed: datetime,
    secret_tokens: list[str],
) -> _RawPage:
    _checked_read_session(
        read_session,
        credentials,
        origin=spec.origin,
        session_binding_sha256=session_binding_sha256,
    )
    owned._client_guard(client)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15
    started = _utc(clock())
    if started < previous_completed:
        _deny("request_clock_reversed")
    request, signature = owned._signed_request(spec, credentials, started, 15)
    secret_tokens.append(signature)
    response = None
    body = bytearray()
    certificate_sha256 = None
    maximum = (
        MAX_CONFIG_BYTES if spec.endpoint == CONFIG_ENDPOINT else history.MAX_PAGE_BYTES
    )
    async with asyncio.timeout_at(deadline):
        if loop.time() >= deadline:
            _deny("request_deadline_exceeded")
        _checked_read_session(
            read_session,
            credentials,
            origin=spec.origin,
            session_binding_sha256=session_binding_sha256,
        )
        response = await client.send(
            request, auth=None, follow_redirects=False, stream=True
        )
        try:
            headers = _utc(clock())
            _checked_read_session(
                read_session,
                credentials,
                origin=spec.origin,
                session_binding_sha256=session_binding_sha256,
            )
            if (
                type(response) is not httpx.Response
                or response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
                or "set-cookie" in response.headers
                or client.cookies
            ):
                _deny("http_response_rejected")
            if type(client._transport) is not httpx.MockTransport:
                network = response.extensions.get("network_stream")
                tls = None if network is None else network.get_extra_info("ssl_object")
                if (
                    type(tls) is not ssl.SSLObject
                    or tls.context is not client._transport._pool._ssl_context
                    or tls.server_hostname != request.url.host
                    or tls.version() not in {"TLSv1.2", "TLSv1.3"}
                ):
                    _deny("tls_evidence_missing")
                certificate = tls.getpeercert(binary_form=True)
                if type(certificate) is not bytes or not certificate:
                    _deny("tls_evidence_missing")
                certificate_sha256 = _hash(certificate)
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
            ):
                _deny("response_media_type_invalid")
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                _deny("response_encoding_rejected")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 10
                or int(length) > maximum
            ):
                _deny("response_bytes_limit")
            async for chunk in response.stream:
                if type(chunk) is not bytes or len(body) + len(chunk) > maximum:
                    _deny("response_bytes_limit")
                body.extend(chunk)
            completed = _utc(clock())
        finally:
            await owned._close(response)
    _checked_read_session(
        read_session,
        credentials,
        origin=spec.origin,
        session_binding_sha256=session_binding_sha256,
    )
    if not started <= headers <= completed or client.cookies:
        _deny("request_clock_reversed")
    if length is not None and len(body) != int(length):
        _deny("response_length_mismatch")
    raw = bytes(body)
    owned._no_secrets(raw, secret_tokens)
    try:
        _, canonical = capture._decode_json(raw, limit=maximum, wire=True)
    except capture.AccountCaptureError:
        _deny("response_json_invalid")
    owned._no_secret_json(canonical, secret_tokens)
    return _RawPage(raw, started, headers, completed, certificate_sha256)


async def collect_demo_position_history(
    *,
    credentials: owned.DemoAccountCredentials,
    read_session: DemoPinnedReadSession,
    clock: Callable[[], datetime],
    plan: history.PositionHistoryPlanV1,
    identity: DemoPositionIdentityV1,
    barrier_completed_at: datetime,
) -> PositionHistoryOwnedArchiveV1:
    """Acquire config / uTime pages / config, then replay from retained bytes.

    A runtime-owned pinned Demo read session must issue the binding ID and
    retain the exact signing credentials across every await. The only
    injectable I/O seam is the private factory used by synthetic tests.
    The returned archive remains a DENY-only diagnostic even after a real GET.
    """
    client = None
    cancelled = False
    try:
        if (
            type(plan) is not history.PositionHistoryPlanV1
            or type(identity) is not DemoPositionIdentityV1
        ):
            _deny("binding_invalid")
        plan.__post_init__()
        if identity.sha256 != plan.account_binding_sha256:
            _deny("binding_invalid")
        values = owned._credential_values(credentials)
        credentials = owned.DemoAccountCredentials(*values)
        _checked_read_session(
            read_session,
            credentials,
            origin=plan.origin,
            session_binding_sha256=plan.session_binding_sha256,
        )
        barrier = _utc(barrier_completed_at)
        if not callable(clock) or barrier < plan.created_at:
            _deny("barrier_invalid")
        first_clock = _utc(clock())
        if first_clock <= barrier:
            _deny("barrier_not_crossed")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MAX_BATCH_SECONDS
        client = _new_client()
        owned._client_guard(client)
        secrets = [
            credentials.api_key,
            credentials.api_secret,
            credentials.passphrase,
            read_session.binding_id,
        ]
        config_spec = _GetSpec(plan.origin, CONFIG_ENDPOINT, ())
        async with asyncio.timeout_at(deadline):
            before = await _signed_get(
                client,
                credentials,
                read_session,
                plan.session_binding_sha256,
                clock,
                config_spec,
                previous_completed=first_clock,
                secret_tokens=secrets,
            )
            _config_page(before, plan, identity)
            pages = []
            certs = []
            after_cursor = None
            previous = None
            previous_completed = before.completed
            for index in range(history.MAX_PAGES):
                method, origin, endpoint, parameters = history.request_for_page(
                    plan, after_cursor
                )
                if method != "GET":
                    _deny("request_invalid")
                raw = await _signed_get(
                    client,
                    credentials,
                    read_session,
                    plan.session_binding_sha256,
                    clock,
                    _GetSpec(origin, endpoint, parameters),
                    previous_completed=previous_completed,
                    secret_tokens=secrets,
                )
                page = history.parse_page(
                    plan,
                    raw.raw,
                    page_index=index,
                    after=after_cursor,
                    previous_page_sha256=None
                    if previous is None
                    else previous.page_sha256,
                    request_started_at=raw.started,
                    headers_received_at=raw.headers,
                    body_completed_at=raw.completed,
                )
                pages.append(page)
                certs.append(raw.certificate_sha256)
                previous_completed, previous = raw.completed, page
                if page.terminal:
                    break
                after_cursor = page.next_after
            chain = history.verify_chain(plan, tuple(pages))
            after = await _signed_get(
                client,
                credentials,
                read_session,
                plan.session_binding_sha256,
                clock,
                config_spec,
                previous_completed=previous_completed,
                secret_tokens=secrets,
            )
            _config_page(after, plan, identity)
            # Later signatures must not have appeared in an earlier page.
            for raw, limit in (
                (before.raw, MAX_CONFIG_BYTES),
                *((page.raw_body, history.MAX_PAGE_BYTES) for page in pages),
                (after.raw, MAX_CONFIG_BYTES),
            ):
                owned._no_secrets(raw, secrets)
                _, canonical = capture._decode_json(raw, limit=limit, wire=True)
                owned._no_secret_json(canonical, secrets)
            archive = freeze_source_bound_archive(
                plan, identity, before, tuple(pages), after, tuple(certs)
            )
            result = verify_source_bound_archive(
                plan, identity, archive, expected_sha256=_hash(archive)
            )
            if result.chain != chain:
                _deny("readback_mismatch")
            _checked_read_session(
                read_session,
                credentials,
                origin=plan.origin,
                session_binding_sha256=plan.session_binding_sha256,
            )
        if loop.time() > deadline:
            _deny("batch_deadline_exceeded")
        return result
    except asyncio.CancelledError:
        cancelled = True
        raise
    except OwnedPositionHistoryError:
        raise
    except (
        owned.AccountCollectionError,
        history.PositionHistoryError,
        capture.AccountCaptureError,
    ):
        _deny("source_rejected")
    except Exception:  # noqa: BLE001 - never stringify credential-bearing errors
        _deny("source_failed")
    finally:
        if client is not None and type(client) is httpx.AsyncClient:
            await owned._close(client, pending_cancel=cancelled)
