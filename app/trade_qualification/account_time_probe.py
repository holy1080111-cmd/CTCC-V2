"""Exact, unauthenticated OKX public-time probe used by account provenance."""

from __future__ import annotations

import asyncio
import re
import ssl
from typing import Annotated, Literal, get_args, get_origin

import httpcore
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    PublicReceiptError,
    _plain,
    canonical,
    decode,
    sha,
    validate_stamps,
)

TIME_ENDPOINT = "/api/v5/public/time"
ORIGIN = "https://openapi.okx.com"
MAX_TIME_BODY = 4096
_SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AccountTimeProbeContract(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    @model_validator(mode="before")
    @classmethod
    def plain_only(cls, value, info):
        _plain(value)
        if type(value) is dict:
            if any(
                name in value
                and (type(value[name]) is not bool or value[name] is not False)
                for name in ("predictive_oos_eligible", "execution_authority")
            ):
                raise PublicReceiptError("authority_forbidden")
            if info.mode == "json":
                value = dict(value)
                for name, field in cls.model_fields.items():
                    if name in value:
                        value[name] = _json_tuple(value[name], field.annotation)
        return value

    def canonical_bytes(self):
        return canonical(self.model_dump(mode="json"))

    def canonical_sha256(self):
        return sha(self.canonical_bytes())


class AccountTimeProbePlanV1(AccountTimeProbeContract):
    schema_version: Literal["ctcc.demo_account_exchange_time_plan.v1"] = (
        "ctcc.demo_account_exchange_time_plan.v1"
    )
    origin: Literal["https://openapi.okx.com"] = ORIGIN
    endpoint: Literal["/api/v5/public/time"] = TIME_ENDPOINT
    created_ns: int = Field(ge=0, le=32_503_680_000_000_000_000)
    max_response_bytes: Literal[4096] = MAX_TIME_BODY
    execution_authority: Literal[False] = False


def _json_tuple(value, annotation):
    if get_origin(annotation) is not tuple or type(value) is not list:
        return value
    args = get_args(annotation)
    if len(args) == 2 and args[1] is Ellipsis:
        return tuple(_json_tuple(item, args[0]) for item in value)
    if len(value) == len(args):
        return tuple(
            _json_tuple(item, item_type)
            for item, item_type in zip(value, args, strict=True)
        )
    return value


class AccountTimeProbeReceiptV1(AccountTimeProbeContract):
    schema_version: Literal["ctcc.demo_account_exchange_time_receipt.v1"] = (
        "ctcc.demo_account_exchange_time_receipt.v1"
    )
    plan_sha256: _SHA
    endpoint: Literal["/api/v5/public/time"] = TIME_ENDPOINT
    query: tuple[tuple[str, str], ...] = ()
    page_index: Literal[0] = 0
    previous_page_sha256: None = None
    cursor_type: None = None
    first_row_identity: None = None
    last_row_identity: None = None
    timestamp_semantics: Literal["server_response_time"] = "server_response_time"
    body_sha256: _SHA
    body_size: int = Field(ge=1, le=MAX_TIME_BODY)
    canonical_body_sha256: _SHA
    request_start: dict
    headers_received: dict
    body_complete: dict
    validation_complete: dict
    tls_peer_sha256: _SHA
    tls_hostname: Literal["openapi.okx.com"] = "openapi.okx.com"
    tls_version: Literal["TLSv1.2", "TLSv1.3"]
    response_headers: tuple[tuple[str, str], ...]
    transport_origin: Literal["owned_native_tls", "synthetic_test"]

    @model_validator(mode="after")
    def causal_stamps_and_safe_headers(self):
        validate_stamps(
            (
                self.request_start,
                self.headers_received,
                self.body_complete,
                self.validation_complete,
            )
        )
        if (
            self.query
            or len(self.response_headers) > 16
            or any(
                type(pair) is not tuple
                or len(pair) != 2
                or pair[0]
                not in {"content-type", "content-length", "content-encoding", "date"}
                or len(pair[1]) > 1024
                for pair in self.response_headers
            )
            or len({name for name, _ in self.response_headers})
            != len(self.response_headers)
        ):
            raise PublicReceiptError("time_probe_receipt_invalid")
        return self


def verify_account_time(plan, receipt, raw):
    if (
        type(plan) is not AccountTimeProbePlanV1
        or type(receipt) is not AccountTimeProbeReceiptV1
    ):
        raise PublicReceiptError("exact_contract_required")
    plan = AccountTimeProbePlanV1.model_validate(plan.model_dump())
    receipt = AccountTimeProbeReceiptV1.model_validate(receipt.model_dump())
    if type(raw) is not bytes or not 0 < len(raw) <= plan.max_response_bytes:
        raise PublicReceiptError("time_probe_raw_invalid")
    if (
        receipt.plan_sha256 != plan.canonical_sha256()
        or receipt.endpoint != plan.endpoint
        or receipt.tls_hostname != plan.origin.removeprefix("https://")
        or sha(raw) != receipt.body_sha256
        or len(raw) != receipt.body_size
    ):
        raise PublicReceiptError("time_probe_identity_mismatch")
    parsed = decode(raw, plan.max_response_bytes)
    if sha(canonical(parsed)) != receipt.canonical_body_sha256:
        raise PublicReceiptError("time_probe_canonical_identity_mismatch")
    if (
        type(parsed) is not dict
        or set(parsed) != {"code", "msg", "data"}
        or parsed["code"] != "0"
        or parsed["msg"] != ""
        or type(parsed["data"]) is not list
        or len(parsed["data"]) != 1
        or type(parsed["data"][0]) is not dict
        or set(parsed["data"][0]) != {"ts"}
    ):
        raise PublicReceiptError("time_response_invalid")
    value = parsed["data"][0]["ts"]
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{12}", value) is None:
        raise PublicReceiptError("time_response_invalid")
    server_ns = int(value) * 1_000_000
    if not (
        receipt.request_start["utc_ns"] <= server_ns <= receipt.body_complete["utc_ns"]
    ):
        raise PublicReceiptError("exchange_time_outside_request")
    if receipt.request_start["utc_ns"] < plan.created_ns:
        raise PublicReceiptError("plan_not_yet_created")
    return server_ns


def _new_client():
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
        follow_redirects=False,
        auth=None,
    )


def _client_guard(client, *, first=False):
    if (
        type(client) is not httpx.AsyncClient
        or client.is_closed
        or client.trust_env
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
        or any(value is not None for value in client._mounts.values())
    ):
        raise PublicReceiptError("public_client_not_isolated")
    transport = client._transport
    if type(transport) is httpx.MockTransport:
        return False
    if (
        type(transport) is not httpx.AsyncHTTPTransport
        or type(transport._pool) is not httpcore.AsyncConnectionPool
        or type(transport._pool._retries) is not int
        or transport._pool._retries != 0
    ):
        raise PublicReceiptError("public_transport_not_isolated")
    context = transport._pool._ssl_context
    if (
        type(context) is not ssl.SSLContext
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise PublicReceiptError("verified_tls_required")
    return True


async def _close(resource, *, cancelled=False):
    async def close_once():
        async with asyncio.timeout(2):
            await resource.aclose()

    task = asyncio.create_task(close_once())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- bounded cleanup, no raw diagnostics
            break
    failed = False
    try:
        task.result()
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- bounded cleanup
        failed = True
    if cancelled:
        raise asyncio.CancelledError
    if failed:
        raise PublicReceiptError("public_cleanup_failed")


async def _fetch_time(client, plan, *, attempt=None):
    """One GET to the fixed unauthenticated public-time endpoint; never retries."""
    if type(plan) is not AccountTimeProbePlanV1:
        raise PublicReceiptError("exact_contract_required")
    native = _client_guard(client)
    request = httpx.Request(
        "GET",
        ORIGIN + TIME_ENDPOINT,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CTCC-account-time-probe/1",
        },
        extensions={"timeout": httpx.Timeout(5).as_dict()},
    )
    body = bytearray()
    started = native_stamp()
    if started["utc_ns"] < plan.created_ns:
        raise PublicReceiptError("plan_not_yet_created")
    if attempt is not None:
        attempt.begin_request(endpoint=TIME_ENDPOINT, query=(), stamp=started)
    response = receipt = None
    accepted, cleanup, error, interrupted, validated_at = (
        False,
        "not_returned",
        None,
        False,
        None,
    )
    try:
        async with asyncio.timeout(5):
            response = await client.send(
                request, auth=None, follow_redirects=False, stream=True
            )
            if type(response) is not httpx.Response:
                response = None
                raise PublicReceiptError("public_response_rejected")
            headers_at = native_stamp()
            rejection = None
            if (
                response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
            ):
                rejection = "public_response_rejected"
            peer, version = "0" * 64, "TLSv1.3"
            tls = {
                "classification": "synthetic_test",
                "peer_sha256": None,
                "hostname": request.url.host,
                "version": None,
            }
            if native:
                tls["classification"] = "unverified"
                try:
                    stream = response.extensions.get("network_stream")
                    ssl_object = (
                        None if stream is None else stream.get_extra_info("ssl_object")
                    )
                    if (
                        type(ssl_object) is not ssl.SSLObject
                        or ssl_object.context
                        is not client._transport._pool._ssl_context
                        or ssl_object.server_hostname != "openapi.okx.com"
                        or ssl_object.version() not in {"TLSv1.2", "TLSv1.3"}
                    ):
                        raise PublicReceiptError("public_tls_peer_missing")
                    cert = ssl_object.getpeercert(binary_form=True)
                    if type(cert) is not bytes or not cert:
                        raise PublicReceiptError("public_tls_peer_missing")
                    peer, version = sha(cert), ssl_object.version()
                    tls = {
                        "classification": "owned_native_tls",
                        "peer_sha256": peer,
                        "hostname": "openapi.okx.com",
                        "version": version,
                    }
                except (ValueError, OSError, AttributeError):
                    rejection = rejection or "public_tls_peer_missing"
            headers = tuple(
                (name, value)
                for name, value in response.headers.multi_items()
                if name
                in {"content-type", "content-length", "content-encoding", "date"}
            )
            if (
                len(headers) > 16
                or any(len(value) > 1024 for _, value in headers)
                or len({name for name, _ in headers}) != len(headers)
            ):
                rejection = rejection or "public_headers_invalid"
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
                or response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                rejection = rejection or "public_encoding_rejected"
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 8
                or int(length) > plan.max_response_bytes
            ):
                rejection = rejection or "public_body_limit"
            if attempt is not None:
                attempt.headers(
                    stamp=headers_at,
                    status=response.status_code,
                    headers=tuple((name, value[:1024]) for name, value in headers[:16]),
                    tls=tls,
                    truncated=len(headers) > 16
                    or any(len(value) > 1024 for _, value in headers),
                )
            if response.is_closed or response.is_stream_consumed:
                raise PublicReceiptError(rejection or "public_response_rejected")
            async for chunk in response.stream:
                if type(chunk) is not bytes:
                    raise PublicReceiptError("public_body_chunk_invalid")
                if attempt is not None:
                    attempt.chunk(chunk, native_stamp())
                if len(body) + len(chunk) > plan.max_response_bytes:
                    raise PublicReceiptError("public_body_limit")
                body.extend(chunk)
            body_at = native_stamp()
            if attempt is not None:
                attempt.body_complete(body_at)
            if rejection is not None:
                raise PublicReceiptError(rejection)
            if length is not None and int(length) != len(body):
                raise PublicReceiptError("public_body_length_mismatch")
            raw = bytes(body)
            canonical_body = canonical(decode(raw, plan.max_response_bytes))
            validated_at = native_stamp()
            receipt = AccountTimeProbeReceiptV1(
                plan_sha256=plan.canonical_sha256(),
                body_sha256=sha(raw),
                body_size=len(raw),
                canonical_body_sha256=sha(canonical_body),
                request_start=started,
                headers_received=headers_at,
                body_complete=body_at,
                validation_complete=validated_at,
                tls_peer_sha256=peer,
                tls_version=version,
                response_headers=headers,
                transport_origin="owned_native_tls" if native else "synthetic_test",
            )
            verify_account_time(plan, receipt, raw)
            accepted = True
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- retain fixed safe trace
        error = exc
        interrupted = isinstance(exc, asyncio.CancelledError)
    finally:
        if response is not None:
            try:
                await _close(response, cancelled=interrupted)
                cleanup = "closed"
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- preserve earliest failure
                error = error or exc
                cleanup = "failed"
        if attempt is not None:
            attempt.finish_request(
                accepted=accepted and error is None,
                validation_complete=validated_at if accepted else None,
                error_code="none" if error is None else "time_probe_rejected",
                cleanup=cleanup,
            )
    if error is not None:
        if interrupted:
            raise asyncio.CancelledError
        raise PublicReceiptError("account_time_probe_rejected") from None
    return receipt, bytes(body)
