"""Bounded private GET DATA used by the shared REST client, never a permit.

Signing and dispatch remain in private_rest._request. A page is not account
completeness; the native reconciliation owner must prove every required chain.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.exchange.okx import native_reconciliation_network_custody_v1 as network_custody

MAX_BODY = 524288
MAX_REQUESTS = 256
MAX_BATCH_BYTES = 67108864
ALGO_TYPES = (
    "conditional",
    "oco",
    "chase",
    "trigger",
    "move_order_stop",
    "iceberg",
    "twap",
    "smart_iceberg",
)
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_CLIENT_ID = re.compile(r"[A-Za-z0-9]{1,32}\Z")
_INSTRUMENT = re.compile(r"[A-Z0-9-]{1,64}\Z")
_TIMESTAMP = re.compile(r"[1-9][0-9]{0,15}\Z")


def require(condition):
    if not condition:
        raise ValueError("native_reconciliation_read_data_invalid")


def stamp():
    return datetime.now(UTC), time.monotonic_ns()


def checked_query(path, params):
    """Exact GET vocabulary; never accepts a method, body or write route."""
    require(
        type(path) is str
        and type(params) is dict
        and all(type(k) is str and type(v) is str for k, v in params.items())
    )
    query = dict(params)
    if path in {
        "/api/v5/account/config",
        "/api/v5/account/balance",
        "/api/v5/account/positions",
        "/api/v5/account/account-position-risk",
    }:
        require(not query)  # Whole account: no selected-symbol/currency filter.
    elif path == "/api/v5/trade/order":
        require(
            set(query) == {"instId", "clOrdId"}
            and _INSTRUMENT.fullmatch(query["instId"]) is not None
            and _CLIENT_ID.fullmatch(query["clOrdId"]) is not None
        )
    elif path == "/api/v5/account/leverage-info":
        require(
            set(query) == {"instId", "mgnMode"}
            and _INSTRUMENT.fullmatch(query["instId"]) is not None
            and query["mgnMode"] == "isolated"
        )
    elif path == "/api/v5/trade/order-algo":
        # Only the exact previously observed attached protection ID. The
        # native owner supplies that original ID; this vocabulary is DATA.
        require(set(query) == {"algoId"} and _ID.fullmatch(query["algoId"]) is not None)
    else:
        after = query.pop("after", None)
        require(after is None or _ID.fullmatch(after) is not None)
        require(query.pop("limit", None) == "100")
        if path == "/api/v5/trade/orders-pending":
            require(not query)
        elif path == "/api/v5/trade/orders-algo-pending":
            require(set(query) == {"ordType"} and query["ordType"] in ALGO_TYPES)
        elif path in {"/api/v5/trade/fills", "/api/v5/trade/fills-history"}:
            require(
                query.get("instType") == "SWAP"
                and _INSTRUMENT.fullmatch(query.get("instId", "")) is not None
            )
            if set(query) == {"instType", "instId", "ordId"}:
                require(_ID.fullmatch(query["ordId"]) is not None)
            else:
                # New negative close/history DATA purpose: a fixed bounded
                # same-instrument record-generation window, never all-time.
                require(
                    set(query) == {"instType", "instId", "begin", "end"}
                    and _TIMESTAMP.fullmatch(query["begin"]) is not None
                    and _TIMESTAMP.fullmatch(query["end"]) is not None
                    and int(query["begin"]) <= int(query["end"])
                )
        elif path == "/api/v5/account/bills-archive":
            require(
                set(query) == {"begin", "end"}
                and _TIMESTAMP.fullmatch(query["begin"]) is not None
                and _TIMESTAMP.fullmatch(query["end"]) is not None
                and int(query["begin"]) <= int(query["end"])
            )
        else:
            require(False)
    return tuple(sorted(params.items()))


@dataclass(frozen=True, slots=True, repr=False)
class DemoReconciliationObservationV1:
    path: str = field(repr=False)
    parameters: tuple = field(repr=False)
    request_url: str = field(repr=False)
    request_signature_sha256: str = field(repr=False)
    read_session_id: str = field(repr=False)
    started_at: datetime = field(repr=False)
    started_monotonic_ns: int = field(repr=False)
    headers_at: datetime = field(repr=False)
    headers_monotonic_ns: int = field(repr=False)
    eof_at: datetime = field(repr=False)
    eof_monotonic_ns: int = field(repr=False)
    http_status: int = field(repr=False)
    raw_headers: tuple = field(repr=False)
    raw_body: bytes = field(repr=False)
    certificate_sha256: str = field(repr=False)
    tls_hostname: str = field(repr=False)
    account_complete: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(slots=True, repr=False)
class ReadJournalV1:
    """Strong bounded memory custody, including partial/late failed reads.

    A constructor is DATA, never native source eligibility or DB durability.
    The actual client's private list retains this exact object before dispatch.
    """

    path: str
    parameters: tuple
    request_url: str
    read_session_id: str
    started: tuple = field(default_factory=stamp)
    signature: str | None = field(default=None, repr=False)
    headers_stamp: tuple | None = None
    eof_stamp: tuple | None = None
    status: int | None = None
    raw_headers: tuple = field(default=(), repr=False)
    chunks: list = field(default_factory=list, repr=False)
    body_bytes: int = 0
    certificate_sha256: str | None = None
    tls_hostname: str | None = None
    declared_length: int | None = None
    faults: list = field(default_factory=list)
    observation: DemoReconciliationObservationV1 | None = field(
        default=None, repr=False
    )
    transport_client: object = field(default=None, repr=False)
    prepared_request: object = field(default=None, repr=False)
    response: object = field(default=None, repr=False)
    network_stream: object = field(default=None, repr=False)
    tls_object: object = field(default=None, repr=False)
    network_backend: object = field(default=None, repr=False)
    tcp_stream: object = field(default=None, repr=False)
    socket_stream: object = field(default=None, repr=False)
    tcp_transport: object = field(default=None, repr=False)
    raw_socket: object = field(default=None, repr=False)
    tls_stream: object = field(default=None, repr=False)
    raw_transport_proxy: object = field(default=None, repr=False)
    cleanup_hold: object = field(default=None, repr=False)
    cleanup_abort: object = field(default=None, repr=False)
    cleanup_abort_code: object = field(default=None, repr=False)

    def fault(self, code):
        require(code in {"response_invalid", "read_failed", "close_failed"})
        if code not in self.faults:
            self.faults.append(code)

    def retain_headers(self, response):
        require(type(response) is httpx.Response and self.headers_stamp is None)
        self.status = response.status_code
        self.headers_stamp = stamp()
        retained, size = [], 0
        for key, value in response.headers.raw:
            require(type(key) is bytes and type(value) is bytes)
            if len(retained) == 256 or size + len(key) + len(value) > 32768:
                self.raw_headers = tuple(retained)
                self.fault("response_invalid")
                require(False)
            retained.append((key, value))
            size += len(key) + len(value)
        self.raw_headers = tuple(retained)

    def retain_chunk(self, chunk):
        require(
            type(chunk) is bytes
            and self.eof_stamp is None
            and len(self.chunks) < MAX_BODY
            and self.body_bytes + len(chunk) <= MAX_BODY
        )
        if chunk:
            self.chunks.append(chunk)
            self.body_bytes += len(chunk)

    def retain_eof(self):
        require(self.eof_stamp is None)
        self.eof_stamp = stamp()

    def finish(self):
        require(
            not self.faults
            and self.observation is None
            and self.status == 200
            and type(self.signature) is str
            and bool(self.signature)
            and self.headers_stamp is not None
            and self.eof_stamp is not None
            and self.started[0] <= self.headers_stamp[0] <= self.eof_stamp[0]
            and self.started[1] <= self.headers_stamp[1] <= self.eof_stamp[1]
            and self.body_bytes > 0
            and self.certificate_sha256 is not None
            and self.tls_hostname is not None
            and (
                self.declared_length is None or self.declared_length == self.body_bytes
            )
        )
        self.observation = DemoReconciliationObservationV1(
            self.path,
            self.parameters,
            self.request_url,
            hashlib.sha256(self.signature.encode("ascii")).hexdigest(),
            self.read_session_id,
            *self.started,
            *self.headers_stamp,
            *self.eof_stamp,
            self.status,
            self.raw_headers,
            b"".join(self.chunks),
            self.certificate_sha256,
            self.tls_hostname,
        )
        return self.observation


def begin(client, lease, path, params, url, *, transport_client):
    parameters = checked_query(path, params)
    journal = ReadJournalV1(path, parameters, url, lease.binding_id)
    # Retain the actual local HTTPX owner before dispatch/any await. A canceled
    # response/client close must not depend on an unwound coroutine or GC for
    # socket custody. This is DATA retention, never transport permission.
    require(type(transport_client) is httpx.AsyncClient)
    journal.transport_client = transport_client
    previous = getattr(client, "_native_reconciliation_read_journals_v1", None)
    if previous is None:
        previous = []
        client._native_reconciliation_read_journals_v1 = previous
    require(
        type(previous) is list
        and len(previous) < MAX_REQUESTS
        and all(type(item) is ReadJournalV1 for item in previous)
        and sum(item.body_bytes for item in previous) + MAX_BODY <= MAX_BATCH_BYTES
    )
    previous.append(journal)
    network_custody.install_fixed_read_network_custody_v1(journal)
    return journal


def retain_prepared_request_v1(journal, request):
    """Keep the actual signed GET before send/TLS/headers can fail."""
    require(
        type(journal) is ReadJournalV1
        and journal.prepared_request is None
        and type(request) is httpx.Request
        and request.method == "GET"
        and str(request.url) == journal.request_url
        and request.content == b""
        and request.headers.get("OK-ACCESS-SIGN") == journal.signature
    )
    journal.prepared_request = request


def retained_httpx_request_data_v1(request):
    """Known installed HTTPX DATA only, for native private memory accounting.

    Does not call vendor properties/serializers, Source/clock/DB callbacks, or
    traverse a client/task/engine. Outgoing authentication is counted here;
    it must never be treated as a received secret echo by the native scanner.
    """
    from httpx._urlparse import ParseResult

    require(type(request) is httpx.Request)
    data = vars(request)
    headers, url, stream = data.get("headers"), data.get("url"), data.get("stream")
    require(
        type(headers) is httpx.Headers
        and type(url) is httpx.URL
        and type(stream) is httpx.ByteStream
    )
    parsed = vars(url).get("_uri_reference")
    require(type(parsed) is ParseResult)
    rows = vars(headers).get("_list")
    require(type(rows) is list and len(rows) <= 256)
    values = [data.get("method"), data.get("_content"), vars(stream).get("_stream")]
    for value in tuple.__iter__(parsed):
        require(value is None or type(value) in (str, int))
        if type(value) is str:
            values.append(value)
    for row in rows:
        require(
            type(row) is tuple
            and len(row) == 3
            and all(type(value) is bytes for value in row)
        )
        values.extend(row)
    encoding = vars(headers).get("_encoding")
    require(encoding is None or type(encoding) is str)
    if encoding is not None:
        values.append(encoding)
    require(all(type(value) in (bytes, str) for value in values))
    unique, seen, size = [], set(), 0
    for value in values:
        if id(value) not in seen:
            seen.add(id(value))
            size += len(value) if type(value) is bytes else len(value.encode("utf-8"))
            require(size <= MAX_BODY + 65536)
            unique.append(value)
    return tuple(unique)


def retained_httpx_journal_data_v1(journal):
    """Actual prepared request and Response body/header copies, DATA only."""
    require(type(journal) is ReadJournalV1)
    values = []
    if journal.prepared_request is not None:
        values.extend(retained_httpx_request_data_v1(journal.prepared_request))
    if journal.response is not None:
        require(type(journal.response) is httpx.Response)
        response = vars(journal.response)
        headers = response.get("headers")
        require(type(headers) is httpx.Headers)
        rows = vars(headers).get("_list")
        require(type(rows) is list and len(rows) <= 256)
        for row in rows:
            require(
                type(row) is tuple
                and len(row) == 3
                and all(type(value) is bytes for value in row)
            )
            values.extend(row)
        for name in ("_content", "_text", "_encoding"):
            if name in response:
                value = response[name]
                require(type(value) in (bytes, str))
                values.append(value)
    seen, result = set(), []
    for value in values:
        if id(value) not in seen:
            seen.add(id(value))
            result.append(value)
    return tuple(result)


def retain_transport(journal, client, request, response):
    """Actual response/TLS observation; no caller certificate or passed flag."""
    require(
        type(journal) is ReadJournalV1
        and journal.transport_client is client
        and journal.prepared_request is request
        and type(request) is httpx.Request
        and type(response) is httpx.Response
    )
    journal.prepared_request, journal.response = request, response
    journal.network_stream = response.extensions.get("network_stream")
    journal.retain_headers(response)  # Retain received DATA before later checks.
    require(
        response.status_code == 200
        and response.url == request.url
        and str(request.url) == journal.request_url
        and not response.history
        and not response.is_closed
        and not response.is_stream_consumed
        and "set-cookie" not in response.headers
        and not client.cookies
        and response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        == "application/json"
    )
    network = response.extensions.get("network_stream")
    tls = None if network is None else network.get_extra_info("ssl_object")
    journal.tls_object = tls
    require(
        type(client) is httpx.AsyncClient
        and type(client._transport) is httpx.AsyncHTTPTransport
        and type(tls) is ssl.SSLObject
        and tls.context is client._transport._pool._ssl_context
        and tls.context.check_hostname is True
        and tls.context.verify_mode == ssl.CERT_REQUIRED
        and tls.server_hostname == request.url.host
        and tls.version() in {"TLSv1.2", "TLSv1.3"}
    )
    certificate = tls.getpeercert(binary_form=True)
    require(type(certificate) is bytes and bool(certificate))
    journal.certificate_sha256 = hashlib.sha256(certificate).hexdigest()
    journal.tls_hostname = tls.server_hostname
