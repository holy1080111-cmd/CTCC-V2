"""Explicit Notion delivery adapter for the existing post-submit outbox.

No token discovery, database creation/update, scheduler or order callback exists
here. The caller owns an isolated HTTPX client and injects a SecretStr token and
a reviewed, fixed destination. Only GET schema, filtered POST query, and at most
one POST page are allowed. Direct Request objects never merge client cookies.

A schema/report collision, 429, or any ambiguous create result is uncertain.
Only transient failures of pre-create reads are known-not-created/retryable.
In particular, no 429 is automatically retried: the existing outbox does not
carry Notion's Retry-After minimum. A successful create requires a second query
readback; an empty/eventually-consistent readback never causes another create.

One trusted outbox root and one reviewed destination configuration must be used
by cooperating workers. Notion supplies no proven unique report-id constraint or
exactly-once create guarantee; independent roots, external writers, trashed
pages, and user edits are not made safe by query-before-create. Unknown jobs
still require explicit, quiesced external reconciliation in the outbox.

Official contracts (checked 2026-09-12):
https://developers.notion.com/reference/versioning
https://developers.notion.com/reference/retrieve-a-data-source
https://developers.notion.com/reference/query-a-data-source
https://developers.notion.com/reference/post-page
https://developers.notion.com/reference/page-property-values
https://developers.notion.com/reference/request-limits
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from urllib.parse import unquote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.trade_evidence import outbox, storage
from app.trade_qualification.quote_collector import _close_response, _public_client

ORIGIN = "https://api.notion.com"
API_VERSION = "2026-03-11"
ROLES = ("report_id", "envelope_sha256", "payload_sha256", "metadata_json")
_UUID = re.compile(r"(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})")
_PROPERTY_ID = re.compile(r"(?:[A-Za-z0-9_-]|%[0-9A-F]{2}){1,96}")


class NotionAdapterError(ValueError):
    """Static codes; no token, request headers, remote messages or URLs."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


class NotionPropertyPin(_Model):
    role: Literal["report_id", "envelope_sha256", "payload_sha256", "metadata_json"]
    property_id: Annotated[str, Field(min_length=1, max_length=96)]
    name: Annotated[str, Field(min_length=1, max_length=96)]
    kind: Literal["title", "rich_text"]

    @model_validator(mode="after")
    def valid_pin(self):
        if _PROPERTY_ID.fullmatch(self.property_id) is None:
            raise ValueError("notion_property_id_invalid")
        if self.name != self.name.strip() or any(ord(char) < 32 for char in self.name):
            raise ValueError("notion_property_name_invalid")
        if self.kind != ("title" if self.role == "report_id" else "rich_text"):
            raise ValueError("notion_property_role_type_invalid")
        return self


class NotionDestination(_Model):
    """Reviewed configuration, not auto-discovered or authenticated by its hash."""

    database_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    data_source_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    properties: tuple[NotionPropertyPin, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def complete_schema(self):
        if tuple(pin.role for pin in self.properties) != ROLES:
            raise ValueError("notion_schema_roles_invalid")
        for values in (
            [unquote(pin.property_id) for pin in self.properties],
            [pin.name.casefold() for pin in self.properties],
        ):
            if len(set(values)) != 4:
                raise ValueError("notion_schema_alias")
        return self


class NotionAdapterPolicy(_Model):
    request_timeout_seconds: int = Field(default=3, ge=1, le=5)
    total_timeout_seconds: int = Field(default=8, ge=2, le=8)
    max_response_bytes: int = Field(default=65536, ge=1024, le=131072)

    @model_validator(mode="after")
    def deadlines(self):
        if self.total_timeout_seconds <= self.request_timeout_seconds:
            raise ValueError("notion_deadline_invalid")
        return self


def _copy(value, kind):
    """Admit original exact objects before any Pydantic serializer can erase data."""
    if (
        type(value) is not kind
        or value.__pydantic_extra__
        or value.__pydantic_private__ is not None
        or set(value.__dict__) != set(kind.model_fields)
    ):
        raise NotionAdapterError("notion_contract_invalid")
    data = dict(value.__dict__)
    if kind is NotionDestination:
        pins = data["properties"]
        if type(pins) is not tuple or len(pins) != 4:
            raise NotionAdapterError("notion_contract_invalid")
        data["properties"] = tuple(
            dict(_copy(pin, NotionPropertyPin).__dict__) for pin in pins
        )
    for key, item in data.items():
        if kind is NotionDestination and key == "properties":
            continue
        if type(item) is str and len(item) <= 2048:
            continue
        if type(item) is int and 0 <= item <= 131072:
            continue
        raise NotionAdapterError("notion_contract_invalid")
    try:
        return kind.model_validate(data, strict=True)
    except (ValueError, TypeError, AttributeError):
        raise NotionAdapterError("notion_contract_invalid") from None


def _json(data):
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def destination_sha256(destination: NotionDestination) -> str:
    target = _copy(destination, NotionDestination)
    return _sha(
        _json(
            {"origin": ORIGIN, "version": API_VERSION, **target.model_dump(mode="json")}
        )
    )


def _uuid(value):
    if type(value) is not str or _UUID.fullmatch(value) is None:
        raise NotionAdapterError("notion_response_identity_invalid")
    return value.replace("-", "")


def _bounded_json(raw):
    def bounded_number(text):
        if len(text) > 96:
            raise ValueError("number_bound")
        number = Decimal(text)
        if not number.is_finite() or abs(number.as_tuple().exponent) > 400:
            raise ValueError("number_bound")
        return number

    def visit(value, depth=0, budget=None):
        budget = [8192] if budget is None else budget
        budget[0] -= 1
        if depth > 14 or budget[0] < 0:
            raise ValueError("json_bound")
        if type(value) is dict:
            if len(value) > 128 or any(
                type(key) is not str or len(key) > 256 for key in value
            ):
                raise ValueError("object_bound")
            for item in value.values():
                visit(item, depth + 1, budget)
        elif type(value) is list:
            if len(value) > 128:
                raise ValueError("array_bound")
            for item in value:
                visit(item, depth + 1, budget)
        elif type(value) is str:
            if len(value) > 8192:
                raise ValueError("string_bound")
        elif value is not None and type(value) not in (bool, Decimal):
            raise ValueError("scalar_invalid")

    try:
        data = json.loads(
            raw,
            object_pairs_hook=storage._unique_object,
            parse_int=bounded_number,
            parse_float=bounded_number,
            parse_constant=storage._json_constant,
        )
        visit(data)
        if type(data) is not dict:
            raise ValueError("object_required")
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError, InvalidOperation):
        raise NotionAdapterError("notion_response_json_invalid") from None


def _client(client, *, initial=False):
    try:
        _public_client(client, require_empty_cookies=initial)
        if client.follow_redirects:
            raise ValueError("redirects")
        if type(client._transport) is httpx.AsyncHTTPTransport:
            pool = client._transport._pool
            context = pool._ssl_context
            if (
                type(context) is not ssl.SSLContext
                or context.verify_mode != ssl.CERT_REQUIRED
                or not context.check_hostname
                or pool._uds is not None
            ):
                raise ValueError("tls_or_uds")
    except (ValueError, TypeError, AttributeError, RuntimeError):
        raise NotionAdapterError("notion_client_not_isolated") from None


def _secret(token):
    if type(token) is not SecretStr:
        raise NotionAdapterError("notion_token_invalid")
    value = token.get_secret_value()
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{16,512}", value) is None:
        raise NotionAdapterError("notion_token_invalid")
    return value


def _active(data):
    if data.get("in_trash") is not False or (
        "archived" in data and data["archived"] is not False
    ):
        raise NotionAdapterError("notion_resource_not_active")


def _schema(data, target):
    if (
        data.get("object") != "data_source"
        or _uuid(data.get("id")) != target.data_source_id
    ):
        raise NotionAdapterError("notion_schema_identity_mismatch")
    parent = data.get("parent")
    if (
        type(parent) is not dict
        or parent.get("type") != "database_id"
        or _uuid(parent.get("database_id")) != target.database_id
    ):
        raise NotionAdapterError("notion_schema_parent_mismatch")
    _active(data)
    properties = data.get("properties")
    if type(properties) is not dict or not {
        pin.name for pin in target.properties
    }.issubset(properties):
        raise NotionAdapterError("notion_schema_mismatch")
    for pin in target.properties:
        item = properties[pin.name]
        if (
            type(item) is not dict
            or item.get("id") != pin.property_id
            or item.get("name") != pin.name
            or item.get("type") != pin.kind
            or item.get(pin.kind) != {}
        ):
            raise NotionAdapterError("notion_schema_mismatch")
        if (
            sum(
                type(other) is dict and other.get("id") == pin.property_id
                for other in properties.values()
            )
            != 1
        ):
            raise NotionAdapterError("notion_schema_alias")


def _texts(payload, claim):
    metadata = outbox._wire(payload).decode("utf-8")
    if len(metadata) > 2000:
        raise NotionAdapterError("notion_metadata_too_large")
    return {
        "report_id": payload.report_id,
        "envelope_sha256": claim.envelope_sha256,
        "payload_sha256": _sha(metadata.encode("utf-8")),
        "metadata_json": metadata,
    }


def _page(data, target, texts, *, page_id=None):
    if data.get("object") != "page":
        raise NotionAdapterError("notion_page_invalid")
    identity = _uuid(data.get("id"))
    if page_id is not None and identity != page_id:
        raise NotionAdapterError("notion_page_identity_mismatch")
    _active(data)
    parent = data.get("parent")
    if (
        type(parent) is not dict
        or parent.get("type") != "data_source_id"
        or _uuid(parent.get("data_source_id")) != target.data_source_id
        or _uuid(parent.get("database_id")) != target.database_id
    ):
        raise NotionAdapterError("notion_page_parent_mismatch")
    properties = data.get("properties")
    if type(properties) is not dict or not {
        pin.name for pin in target.properties
    }.issubset(properties):
        raise NotionAdapterError("notion_page_schema_mismatch")
    for pin in target.properties:
        prop = properties[pin.name]
        if (
            type(prop) is not dict
            or prop.get("id") != pin.property_id
            or prop.get("type") != pin.kind
        ):
            raise NotionAdapterError("notion_page_schema_mismatch")
        if (
            sum(
                type(other) is dict and other.get("id") == pin.property_id
                for other in properties.values()
            )
            != 1
        ):
            raise NotionAdapterError("notion_page_schema_alias")
        text = prop.get(pin.kind)
        if type(text) is not list or len(text) != 1 or type(text[0]) is not dict:
            raise NotionAdapterError("notion_page_content_mismatch")
        text = text[0]
        body = text.get("text")
        expected = texts[pin.role]
        if (
            text.get("type") != "text"
            or type(body) is not dict
            or body.get("content") != expected
            or body.get("link") is not None
            or text.get("plain_text") != expected
            or text.get("href") is not None
        ):
            raise NotionAdapterError("notion_page_content_mismatch")
    return identity


def _results(data):
    if (
        data.get("object") != "list"
        or data.get("type") != "page_or_data_source"
        or data.get("has_more") is not False
        or data.get("next_cursor", "missing") is not None
        or type(data.get("results")) is not list
        or len(data["results"]) > 1
    ):
        raise NotionAdapterError("notion_report_collision_or_incomplete")
    return data["results"]


class NotionDeliveryAdapter:
    """Callable for outbox.dispatch_once; owns responses, not the injected client.

    HTTPX 0.28.1's standard verified-TLS transport or exact MockTransport only;
    custom/malicious transports and same-user mutation are not authenticated.
    The transport must honor cancellation and complete its bounded close.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        token: SecretStr,
        destination: NotionDestination,
        policy: NotionAdapterPolicy | None = None,
        clock: Callable[[], datetime] = outbox.actual_utc,
    ):
        _client(client, initial=True)
        _secret(token)
        if not callable(clock):
            raise NotionAdapterError("notion_clock_invalid")
        self._client = client
        self._token = token
        self._destination = _copy(destination, NotionDestination)
        self._policy = _copy(
            NotionAdapterPolicy() if policy is None else policy, NotionAdapterPolicy
        )
        self._clock = clock
        self._busy = False

    async def _request(self, method, path, body, *, policy, claim, proofs, not_before):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        _client(self._client)
        started = outbox._now(self._clock)
        if started >= claim.lease_expires_at:
            raise NotionAdapterError("notion_lease_expired")
        if started < not_before or (proofs and started < proofs[-1]["completed_at"]):
            raise NotionAdapterError("notion_clock_reversed")
        content = b"" if body is None else _json(body)
        request = httpx.Request(
            method,
            ORIGIN + path,
            content=content,
            headers={
                "Authorization": "Bearer " + _secret(self._token),
                "Notion-Version": API_VERSION,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
            },
            extensions={
                "timeout": httpx.Timeout(policy.request_timeout_seconds).as_dict()
            },
        )
        response = None
        pending_cancel = False
        async with asyncio.timeout(policy.request_timeout_seconds) as deadline:
            try:
                response = await self._client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                if task is not None and task.cancelling():
                    raise asyncio.CancelledError
                if len(response.headers.raw) > 128 or (
                    sum(len(key) + len(value) for key, value in response.headers.raw)
                    > 32768
                ):
                    raise NotionAdapterError("notion_response_headers_invalid")
                for header in ("content-type", "content-length", "content-encoding"):
                    if len(response.headers.get_list(header)) > 1:
                        raise NotionAdapterError("notion_response_headers_invalid")
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                ):
                    raise NotionAdapterError("notion_response_media_invalid")
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise NotionAdapterError("notion_response_encoding_invalid")
                length = response.headers.get("content-length")
                if length is not None and (
                    len(length) > 10
                    or not length.isascii()
                    or not length.isdigit()
                    or int(length) > policy.max_response_bytes
                ):
                    raise NotionAdapterError("notion_response_too_large")
                raw = bytearray()
                chunks = 0
                async for chunk in response.stream:
                    chunks += 1
                    if (
                        type(chunk) is not bytes
                        or chunks > 1024
                        or len(raw) + len(chunk) > policy.max_response_bytes
                    ):
                        raise NotionAdapterError("notion_response_too_large")
                    raw.extend(chunk)
            except asyncio.CancelledError:
                pending_cancel = True
                raise
            finally:
                if response is not None:
                    await _close_response(response, pending_cancel=pending_cancel)
        if deadline.expired():
            raise TimeoutError
        completed = outbox._now(self._clock)
        if completed < started or completed >= claim.lease_expires_at:
            raise NotionAdapterError("notion_clock_or_lease_invalid")
        if length is not None and len(raw) != int(length):
            raise NotionAdapterError("notion_response_length_invalid")
        proofs.append(
            {
                "method": method,
                "path": path,
                "request_sha256": _sha(content),
                "status": response.status_code,
                "body_sha256": _sha(bytes(raw)),
                "started_at": started,
                "completed_at": completed,
            }
        )
        if response.status_code != 200:
            if (
                response.status_code >= 500
                and "retry-after" not in response.headers
                and response.status_code != 529
            ):
                raise NotionAdapterError("notion_read_transient")
            raise NotionAdapterError("notion_http_uncertain")
        return _bounded_json(bytes(raw))

    async def __call__(
        self, payload: outbox.OutboxPayload, claim: outbox.ClaimToken
    ) -> outbox.DeliveryOutcome:
        payload, claim = (
            outbox._copy(payload, outbox.OutboxPayload),
            outbox._copy(claim, outbox.ClaimToken),
        )
        target, policy = (
            _copy(self._destination, NotionDestination),
            _copy(self._policy, NotionAdapterPolicy),
        )
        if payload.report_id != claim.report_id or claim.status != "dispatching":
            raise NotionAdapterError("notion_claim_identity_invalid")
        if self._busy:
            raise NotionAdapterError("notion_adapter_busy")
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        started = outbox._now(self._clock)
        if started < payload.submitted_at or started >= claim.lease_expires_at:
            raise NotionAdapterError("notion_clock_or_lease_invalid")
        created = False
        proofs = []
        status, page_id = "uncertain", None
        texts = _texts(payload, claim)
        path = "/v1/data_sources/" + target.data_source_id
        query = {
            "filter": {
                "property": target.properties[0].property_id,
                "title": {"equals": payload.report_id},
            },
            "page_size": 2,
            "result_type": "page",
            "in_trash": False,
        }
        self._busy = True
        try:
            async with asyncio.timeout(policy.total_timeout_seconds) as deadline:
                schema = await self._request(
                    "GET",
                    path,
                    None,
                    policy=policy,
                    claim=claim,
                    proofs=proofs,
                    not_before=started,
                )
                _schema(schema, target)
                found = _results(
                    await self._request(
                        "POST",
                        path + "/query",
                        query,
                        policy=policy,
                        claim=claim,
                        proofs=proofs,
                        not_before=started,
                    )
                )
                if found:
                    page_id = _page(found[0], target, texts)
                else:
                    body = {
                        "parent": {
                            "type": "data_source_id",
                            "data_source_id": target.data_source_id,
                        },
                        "properties": {
                            pin.property_id: {
                                pin.kind: [
                                    {
                                        "type": "text",
                                        "text": {"content": texts[pin.role]},
                                    }
                                ]
                            }
                            for pin in target.properties
                        },
                    }
                    created = (
                        True  # Before any possibly-sent create, not after success.
                    )
                    page_id = _page(
                        await self._request(
                            "POST",
                            "/v1/pages",
                            body,
                            policy=policy,
                            claim=claim,
                            proofs=proofs,
                            not_before=started,
                        ),
                        target,
                        texts,
                    )
                    found = _results(
                        await self._request(
                            "POST",
                            path + "/query",
                            query,
                            policy=policy,
                            claim=claim,
                            proofs=proofs,
                            not_before=started,
                        )
                    )
                    if len(found) != 1:
                        raise NotionAdapterError("notion_create_readback_missing")
                    _page(found[0], target, texts, page_id=page_id)
                status = "delivered"
            if deadline.expired():
                status, page_id = "uncertain", None
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, TimeoutError):
            status = "uncertain" if created else "not_created_retryable"
            page_id = None
        except NotionAdapterError as exc:
            status = (
                "not_created_retryable"
                if not created and exc.code == "notion_read_transient"
                else "uncertain"
            )
            page_id = None
        except Exception:  # noqa: BLE001 - Never disclose transport or response exception text.
            status, page_id = "uncertain", None
        finally:
            self._busy = False
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        completed = outbox._now(self._clock)
        if completed < started or (proofs and completed < proofs[-1]["completed_at"]):
            raise NotionAdapterError("notion_clock_reversed")
        if completed >= claim.lease_expires_at:
            status, page_id = "uncertain", None
        receipt = None
        if status != "uncertain":
            safe_proofs = [
                {
                    **proof,
                    "started_at": proof["started_at"].isoformat(),
                    "completed_at": proof["completed_at"].isoformat(),
                }
                for proof in proofs
            ]
            receipt = _sha(
                _json(
                    {
                        "target_sha256": destination_sha256(target),
                        "report_id": payload.report_id,
                        "envelope_sha256": claim.envelope_sha256,
                        "payload_sha256": texts["payload_sha256"],
                        "fence_token": claim.fence_token,
                        "status": status,
                        "page_id": page_id,
                        "create_attempted": created,
                        "started_at": started.isoformat(),
                        "completed_at": completed.isoformat(),
                        "requests": safe_proofs,
                    }
                )
            )
        return outbox.DeliveryOutcome(
            report_id=payload.report_id,
            envelope_sha256=claim.envelope_sha256,
            fence_token=claim.fence_token,
            status=status,
            remote_page_id=page_id,
            receipt_sha256=receipt,
        )
