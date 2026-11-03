from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.exchange.okx import native_reconciliation_observation_v1 as reconciliation_read
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.transport_observation import (
    ObservedOrderResult,
    observe_order_response,
)
from app.exchange.okx.transport_observation import (
    _utc as observation_utc,
)
from app.okx_live.execution_authority import enforce_live_final_dispatch
from app.trade_qualification.dispatch_ownership import DispatchOwnership, DispatchSlot
from app.trade_qualification.execution_authority import (
    enforce_demo_final_dispatch,
    enforce_demo_submission_boundary,
    enforce_live_submission_boundary,
)
from app.trade_qualification.private_write_authority import (
    require_owned_private_write_authority,
)


@dataclass(frozen=True, slots=True, repr=False)
class DemoConfigSelectorObservationV2:
    """Private selector DATA; never serialize signature/raw response publicly."""

    data: tuple[dict[str, Any], ...] = field(repr=False)
    raw_body: bytes = field(repr=False)
    request_signature: str = field(repr=False)
    account_complete: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _demo_selector_json_pairs_v2(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("demo_selector_duplicate_json")
        result[key] = value
    return result


def _demo_selector_json_constant_v2(_value):
    raise ValueError("demo_selector_nonfinite_json")


def _demo_selector_json_float_v2(value):
    result = float(value)
    if not float("-inf") < result < float("inf"):
        raise ValueError("demo_selector_nonfinite_json")
    return result


def _demo_selector_append_chunk_v2(chunks, size, chunk):
    if (
        type(chunks) is not list
        or type(chunk) is not bytes
        or type(size) is not int
        or not 0 <= size <= 65536
    ):
        raise ValueError("demo_selector_stream_invalid")
    next_size = size + len(chunk)
    if next_size > 65536:
        raise ValueError("demo_selector_response_bound")
    if chunk:
        chunks.append(chunk)
    return next_size


Clock = Callable[[], datetime]
_ALGO_ORDER_TYPES = (
    "conditional",
    "oco",
    "chase",
    "trigger",
    "move_order_stop",
    "iceberg",
    "twap",
    "smart_iceberg",
)
_PRIVATE_PAGE_SIZE = 100
_PRIVATE_MAX_PAGES = 16
_CANCEL_ALL_AFTER_PATH = "/api/v5/trade/cancel-all-after"
_READ_ONLY_HTTP_AUTH_CODES = frozenset({"50110", "50119"})
_MAX_HTTP_AUTH_ERROR_BYTES = 4096
_SAFE_EXCHANGE_ERROR_CODE = re.compile(r"[0-9]{5}(?:_[0-9]{4})?\Z")
_SAFE_ORDER_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_SAFE_CLIENT_ORDER_ID = re.compile(r"[A-Za-z0-9]{1,32}\Z")
_SAFE_INSTRUMENT_ID = re.compile(r"[A-Z0-9-]{1,64}\Z")
_SAFE_LEVERAGE = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,8})?\Z")
_SAFE_PRECHECK_DECIMAL = re.compile(r"-?[0-9]{1,24}(?:\.[0-9]{1,36})?\Z")
_SAFE_PRECHECK_SIZE = re.compile(r"[0-9]{1,24}(?:\.[0-9]{1,36})?\Z")
_SAFE_ORDER_TYPE = re.compile(r"[a-z_]{2,32}\Z")
_SAFE_CAA_TIMESTAMP = re.compile(r"[1-9][0-9]{9,15}\Z")
_SAFE_ORDER_TAG = re.compile(r"[A-Za-z0-9]{0,16}\Z")
_PRECHECK_REQUIRED_FIELDS = (
    "adjEq",
    "adjEqChg",
    "imr",
    "imrChg",
    "mmr",
    "mmrChg",
    "mgnRatio",
    "mgnRatioChg",
)
_WRITE_RESPONSE_PATHS = frozenset(
    {
        "/api/v5/trade/order-precheck",
        "/api/v5/trade/order",
        "/api/v5/trade/cancel-order",
        "/api/v5/trade/close-position",
        "/api/v5/account/set-leverage",
        _CANCEL_ALL_AFTER_PATH,
    }
)


def _ambiguous_write_response() -> OkxPrivateApiError:
    # Never put a remote value, including a syntactically invalid identifier,
    # in this exception. A write may already have changed exchange state.
    return OkxPrivateApiError(
        "OKX private write response requires reconciliation",
        code="ambiguous_response",
    )


def _project_successful_write_data(
    *, path: str, signed_body_text: str, data: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Return only checked, operational fields from one successful write row.

    This is a non-secret API/status projection, not a raw response receipt, an
    exchange-state attestation, or forensic custody. A future qualified writer
    must retain the original response privately before interpreting it. Unknown
    routes and ambiguous identifiers fail closed; arbitrary remote text and
    nested fields never cross into legacy ``exchange_data`` or logs.
    """
    if path not in _WRITE_RESPONSE_PATHS or len(data) != 1:
        raise _ambiguous_write_response()
    try:
        signed_body = json.loads(signed_body_text)
    except (TypeError, ValueError):
        raise _ambiguous_write_response() from None
    if type(signed_body) is not dict:
        raise _ambiguous_write_response()

    row = data[0]
    projected: dict[str, str] = {}
    if "sCode" in row:
        if row["sCode"] != "0" or type(row["sCode"]) is not str:
            raise _ambiguous_write_response()
        projected["sCode"] = "0"

    if path in {"/api/v5/trade/order", "/api/v5/trade/cancel-order"}:
        if "sCode" not in projected:
            raise _ambiguous_write_response()
        order_id = row.get("ordId")
        if order_id not in (None, ""):
            if type(order_id) is not str or not _SAFE_ORDER_ID.fullmatch(order_id):
                raise _ambiguous_write_response()
            requested_order_id = signed_body.get("ordId")
            if requested_order_id is not None and order_id != requested_order_id:
                raise _ambiguous_write_response()
            projected["ordId"] = order_id
        client_order_id = row.get("clOrdId")
        requested_client_order_id = signed_body.get("clOrdId")
        # A cancel-by-ordId response may include an unrelated client ID that
        # was not in the signed request. Drop it rather than exposing it.
        if client_order_id not in (None, "") and requested_client_order_id is not None:
            if (
                type(client_order_id) is not str
                or not _SAFE_CLIENT_ORDER_ID.fullmatch(client_order_id)
                or client_order_id != requested_client_order_id
            ):
                raise _ambiguous_write_response()
            projected["clOrdId"] = client_order_id
        if path == "/api/v5/trade/order":
            if (
                "ordId" not in projected
                or type(requested_client_order_id) is not str
                or not _SAFE_CLIENT_ORDER_ID.fullmatch(requested_client_order_id)
                or projected.get("clOrdId") != requested_client_order_id
            ):
                raise _ambiguous_write_response()
        elif not projected.get("ordId") and not projected.get("clOrdId"):
            raise _ambiguous_write_response()

    if path == "/api/v5/account/set-leverage":
        instrument = row.get("instId")
        margin_mode = row.get("mgnMode")
        position_side = row.get("posSide", "")
        leverage = row.get("lever")
        if type(position_side) is not str:
            raise _ambiguous_write_response()
        expected_position_side = signed_body.get("posSide")
        position_side_matches = (
            position_side in {"", "net"}
            if expected_position_side is None
            else position_side == expected_position_side
        )
        if (
            type(instrument) is not str
            or not _SAFE_INSTRUMENT_ID.fullmatch(instrument)
            or instrument != signed_body.get("instId")
            or type(margin_mode) is not str
            or margin_mode not in {"cross", "isolated"}
            or margin_mode != signed_body.get("mgnMode")
            or position_side not in {"", "net", "long", "short"}
            or not position_side_matches
            or type(leverage) is not str
            or not _SAFE_LEVERAGE.fullmatch(leverage)
        ):
            raise _ambiguous_write_response()
        try:
            matches_leverage = Decimal(leverage) == Decimal(
                str(signed_body.get("lever"))
            )
        except (InvalidOperation, TypeError):
            raise _ambiguous_write_response() from None
        if not matches_leverage:
            raise _ambiguous_write_response()
        projected.update(
            {
                "instId": instrument,
                "mgnMode": margin_mode,
                "posSide": position_side,
                "lever": leverage,
            }
        )

    if path == "/api/v5/trade/close-position":
        # OKX's documented row contains these four keys. An arbitrary object
        # must not become an acknowledged close, and the echoed identity must
        # agree with the signed request. The close still needs reconciliation.
        instrument = row.get("instId")
        position_side = row.get("posSide")
        client_order_id = row.get("clOrdId")
        tag = row.get("tag")
        if (
            type(instrument) is not str
            or not _SAFE_INSTRUMENT_ID.fullmatch(instrument)
            or instrument != signed_body.get("instId")
            or type(signed_body.get("mgnMode")) is not str
            or signed_body.get("mgnMode") not in {"cross", "isolated"}
            or type(position_side) is not str
            or position_side not in {"net", "long", "short"}
            or position_side != signed_body.get("posSide", "net")
            or type(client_order_id) is not str
            or (
                client_order_id != ""
                and not _SAFE_CLIENT_ORDER_ID.fullmatch(client_order_id)
            )
            or client_order_id != signed_body.get("clOrdId", "")
            or type(tag) is not str
            or not _SAFE_ORDER_TAG.fullmatch(tag)
            or tag != signed_body.get("tag", "")
            or ("mgnMode" in row and row["mgnMode"] != signed_body["mgnMode"])
        ):
            raise _ambiguous_write_response()
        projected.update({"instId": instrument, "posSide": position_side})

    if path == "/api/v5/trade/order-precheck":
        # The documented row has margin measurements, but no order identifier
        # to read back. Validate its recognizable schema; this is not proof of
        # the hypothetical trade economics or permission to submit an order.
        if (
            type(signed_body.get("instId")) is not str
            or not _SAFE_INSTRUMENT_ID.fullmatch(signed_body["instId"])
            or type(signed_body.get("tdMode")) is not str
            or signed_body.get("tdMode") not in {"cash", "cross", "isolated"}
            or type(signed_body.get("side")) is not str
            or signed_body.get("side") not in {"buy", "sell"}
            or type(signed_body.get("ordType")) is not str
            or not _SAFE_ORDER_TYPE.fullmatch(signed_body["ordType"])
            or type(signed_body.get("sz")) is not str
            or not _SAFE_PRECHECK_SIZE.fullmatch(signed_body["sz"])
            or Decimal(signed_body["sz"]) <= 0
            or ("instId" in row and row["instId"] != signed_body["instId"])
        ):
            raise _ambiguous_write_response()
        for name in _PRECHECK_REQUIRED_FIELDS:
            value = row.get(name)
            if type(value) is not str or not _SAFE_PRECHECK_DECIMAL.fullmatch(value):
                raise _ambiguous_write_response()
        projected.update({name: row[name] for name in ("adjEqChg", "imrChg", "mmrChg")})

    if path == _CANCEL_ALL_AFTER_PATH:
        # OKX documents triggerTime, ts and tag. Their ordered timestamps and
        # the exact echoed tag identify a timer response, but cannot attest
        # that the timer is still armed when a later order is submitted.
        trigger_time = row.get("triggerTime")
        request_time = row.get("ts")
        tag = row.get("tag")
        requested_timeout = signed_body.get("timeOut")
        if (
            type(trigger_time) is not str
            or not _SAFE_CAA_TIMESTAMP.fullmatch(trigger_time)
            or type(request_time) is not str
            or not _SAFE_CAA_TIMESTAMP.fullmatch(request_time)
            or len(trigger_time) != len(request_time)
            or int(trigger_time) <= int(request_time)
            or type(tag) is not str
            or not _SAFE_ORDER_TAG.fullmatch(tag)
            or tag != signed_body.get("tag", "")
            or type(requested_timeout) is not str
            or re.fullmatch(r"[1-9][0-9]{1,2}", requested_timeout) is None
            or not 10 <= int(requested_timeout) <= 120
            or len(request_time) not in {10, 13}
            or int(trigger_time) - int(request_time)
            != int(requested_timeout) * (1 if len(request_time) == 10 else 1000)
        ):
            raise _ambiguous_write_response()
        projected.update({"triggerTime": trigger_time, "ts": request_time, "tag": tag})

    return [projected]


def _private_response_rejection(code: Any, *, write: bool) -> OkxPrivateApiError:
    """Keep exchange rejection text/data out of API, status, journal and logs.

    A nonzero write response is not proof that the exchange made no change.
    Until a separately retained raw observation resolves it, callers must keep
    the submission intent and exposure uncertain rather than mark a rejection.
    """
    if write:
        return OkxPrivateApiError(
            "OKX private write response requires reconciliation",
            code="ambiguous_response",
        )
    safe_code = (
        code
        if type(code) is str and _SAFE_EXCHANGE_ERROR_CODE.fullmatch(code)
        else "private_response_rejected"
    )
    return OkxPrivateApiError("OKX private read rejected", code=safe_code)


def _read_only_http_auth_code(response: httpx.Response) -> str | None:
    """Recognize a bounded OKX auth code without retaining remote error text.

    A non-2xx body is untrusted. Only two documented 401 codes are useful for
    diagnosing a failed read; all other response bodies retain the old generic
    transport failure. In particular, never forward ``msg`` or ``data`` because
    the IP-whitelist message may contain the caller's public address.
    """
    if (
        response.status_code != 401
        or len(response.content) > _MAX_HTTP_AUTH_ERROR_BYTES
        or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return None
    try:
        payload = json.loads(response.content)
    except (TypeError, ValueError):
        return None
    if type(payload) is not dict:
        return None
    code = payload.get("code")
    return code if type(code) is str and code in _READ_ONLY_HTTP_AUTH_CODES else None


@dataclass(slots=True, repr=False)
class _DemoReadLease:
    active: bool = True


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class DemoPinnedReadSession:
    """Private, GET-only Demo transport snapshot; the ID contains no secrets."""

    _owner: OkxDemoPrivateRestClient = field(repr=False)
    _settings: Settings = field(repr=False)
    _environment: str = field(repr=False)
    _credentials: tuple[str, str, str] = field(repr=False)
    _origin: str = field(repr=False)
    _timeout_seconds: float = field(repr=False)
    _read_max_retries: int = field(repr=False)
    _user_agent: str = field(repr=False)
    _lease: _DemoReadLease = field(repr=False)
    binding_id: str

    def verify_current(self) -> None:
        """Reject a closed/foreign session or any changed source setting."""
        if (
            not self._lease.active
            or _DEMO_READ_SESSION.get() is not self
            or self._owner.settings is not self._settings
        ):
            raise OkxPrivateApiError(
                "Demo read session is no longer active",
                code="demo_read_session_invalid",
            )
        if self._settings.environment != self._environment:
            raise OkxPrivateApiError(
                "Demo read session settings changed",
                code="demo_read_session_settings_changed",
            )
        if self._environment != "test" and (
            self._owner._external_client is not None or self._owner._clock_injected
        ):
            raise OkxPrivateApiError(
                "Demo read transport is not runtime-owned",
                code="demo_read_transport_invalid",
            )
        try:
            current = self._owner._unbound_read_values()
        except Exception:  # noqa: BLE001 - no source or credential value may escape
            raise OkxPrivateApiError(
                "Demo read session settings changed",
                code="demo_read_session_settings_changed",
            ) from None
        if current != (
            self._credentials,
            self._origin,
            self._timeout_seconds,
            self._read_max_retries,
            self._user_agent,
        ):
            raise OkxPrivateApiError(
                "Demo read session settings changed",
                code="demo_read_session_settings_changed",
            )


_DEMO_READ_SESSION: ContextVar[DemoPinnedReadSession | None] = ContextVar(
    "ctcc_okx_demo_pinned_read_session", default=None
)


def _validate_cancel_all_after_dispatch(
    *, method: str, path: str, request_path: str, body_text: str
) -> None:
    """Validate the immutable bytes-to-send after signing and before HTTP IO."""
    if method.upper() != "POST" or path != _CANCEL_ALL_AFTER_PATH:
        return
    try:
        payload = json.loads(body_text)
    except (TypeError, ValueError) as exc:
        raise OkxPrivateApiError(
            "Cancel All After payload is invalid",
            code="cancel_all_after_payload_rejected",
        ) from exc
    if (
        request_path != path
        or type(payload) is not dict
        or set(payload) not in ({"timeOut"}, {"timeOut", "tag"})
        or type(payload.get("timeOut")) is not str
        or re.fullmatch(r"[1-9][0-9]{1,2}", payload["timeOut"]) is None
        or not 10 <= int(payload["timeOut"]) <= 120
        or (
            "tag" in payload
            and (
                type(payload["tag"]) is not str
                or re.fullmatch(r"[A-Za-z0-9]{1,16}", payload["tag"]) is None
            )
        )
    ):
        raise OkxPrivateApiError(
            "Cancel All After payload is invalid",
            code="cancel_all_after_payload_rejected",
        )


def _validated_origin(value: str, hosts: frozenset[str]) -> str:
    origin = httpx.URL(value)
    if (
        origin.scheme != "https"
        or origin.host not in hosts
        or origin.port not in (None, 443)
        or origin.userinfo
        or origin.raw_path not in (b"", b"/")
        or origin.query
        or origin.fragment
    ):
        raise OkxPrivateApiError(
            "Private REST origin is invalid", code="private_transport_target_rejected"
        )
    return str(origin).rstrip("/")


def utc_iso_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_signature(
    *,
    timestamp: str,
    method: str,
    request_path: str,
    body: str,
    secret: str,
) -> str:
    prehash = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(
        secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class _OkxPrivateRestClientBase:
    """Authenticated OKX private REST transport shared by fixed environments.

    Environment-specific subclasses supply credentials and headers. Read requests
    have bounded retries; write requests are never automatically retried to avoid
    duplicate orders after ambiguous network failures.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._external_client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._clock_injected = clock is not None
        self._last_prepared_dispatch_diagnostic = None

    @property
    def last_prepared_dispatch_diagnostic(self):
        """Non-secret, DENY-only observation; never a submission permit."""
        return self._last_prepared_dispatch_diagnostic

    def _credentials(self) -> tuple[str, str, str]:
        raise NotImplementedError

    def _rest_base_url(self) -> str:
        raise NotImplementedError

    def _timeout_seconds(self) -> float:
        raise NotImplementedError

    def _read_max_retries(self) -> int:
        raise NotImplementedError

    def _extra_headers(self) -> dict[str, str]:
        return {}

    def _user_agent(self) -> str:
        return f"CTCC-V2/{self.settings.app_version}"

    def _after_response_check(self, *, method: str, path: str) -> None:
        """A private reader may reject settings changed during network IO."""

    def _request_extra_headers(
        self, *, method: str, path: str, write: bool
    ) -> dict[str, str]:
        return {}

    def _before_send(self, *, method: str, path: str) -> None:
        """Synchronous application check; the HTTP client still has internal awaits."""

    def _final_dispatch_check(self, *, method: str, path: str) -> None:
        """Environment-specific authority after signing, immediately before HTTP."""

    def _validate_external_client(self, client: httpx.AsyncClient) -> None:
        """Injected clients are a bounded, network-free test adapter only.

        Runtime clients are owned here. An injected client may not change signed
        requests through headers, default query/cookies, auth, hooks or mounted
        network transports. Recheck after signing on every attempt.
        """
        if self._external_client is None:
            return
        allowed_headers = {"accept", "accept-encoding", "connection", "user-agent"}
        if (
            type(client) is not httpx.AsyncClient
            or type(client._transport) is not httpx.MockTransport
            or any(value is not None for value in client._mounts.values())
            or client.auth is not None
            or any(client.event_hooks.values())
            or set(client.headers) - allowed_headers
            or client.params
            or client.cookies
        ):
            raise OkxPrivateApiError(
                "Injected private transport is not a controlled test adapter",
                code="private_transport_adapter_rejected",
            )

    def _headers(self, *, method: str, request_path: str, body: str) -> dict[str, str]:
        api_key, api_secret, passphrase = self._credentials()
        timestamp = utc_iso_timestamp(self._clock())
        signature = build_signature(
            timestamp=timestamp,
            method=method,
            request_path=request_path,
            body=body,
            secret=api_secret,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent(),
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "OK-ACCESS-TIMESTAMP": timestamp,
        }
        headers.update(self._extra_headers())
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
        observe_order: bool = False,
        _demo_selector_max_bytes: int | None = None,
        _demo_reconciliation_max_bytes: int | None = None,
        _native_configuration_max_bytes: int | None = None,
    ) -> (
        list[dict[str, Any]]
        | ObservedOrderResult
        | DemoConfigSelectorObservationV2
        | reconciliation_read.DemoReconciliationObservationV1
    ):
        demo_selector = _demo_selector_max_bytes is not None
        demo_reconciliation = _demo_reconciliation_max_bytes is not None
        bounded_read = demo_selector or demo_reconciliation
        demo_configuration = _native_configuration_max_bytes is not None
        bounded_response = bounded_read or demo_configuration
        if demo_configuration:
            from app.exchange.okx import native_demo_write_v2 as native_configuration

            if (
                type(_native_configuration_max_bytes) is not int
                or _native_configuration_max_bytes != 65536
                or type(self) is not OkxDemoPrivateRestClient
                or method != "POST"
                or path != "/api/v5/account/set-leverage"
                or params is not None
                or type(body) is not dict
                or write is not True
                or observe_order is not False
                or bounded_read
                or self._external_client is not None
                or self._clock_injected is not False
                or not native_configuration._native_leverage_configuration_route_v1(
                    self, method, path
                )
            ):
                raise OkxPrivateApiError(
                    "Native configuration write unavailable",
                    code="native_configuration_write_invalid",
                )
        if demo_reconciliation:
            reconciliation_read.checked_query(path, params)
            if (
                _demo_reconciliation_max_bytes != reconciliation_read.MAX_BODY
                or type(_demo_reconciliation_max_bytes) is not int
                or type(self) is not OkxDemoPrivateRestClient
                or method != "GET"
                or body is not None
                or write is not False
                or observe_order is not False
                or self._external_client is not None
                or self._clock_injected is not False
                or self._active_read_session() is None
                or self._read_max_retries() != 0
            ):
                raise OkxPrivateApiError(
                    "Native reconciliation GET is unavailable",
                    code="native_reconciliation_read_invalid",
                )
        if demo_selector and (
            type(_demo_selector_max_bytes) is not int
            or _demo_selector_max_bytes != 65536
            or type(self) is not OkxDemoPrivateRestClient
            or method != "GET"
            or path != "/api/v5/account/config"
            or params is not None
            or body is not None
            or write is not False
            or observe_order is not False
            or self._external_client is not None
            or self._clock_injected is not False
            or self._active_read_session() is None
            or self._read_max_retries() != 0
        ):
            raise OkxPrivateApiError(
                "Demo selector purpose is unavailable",
                code="demo_selector_purpose_invalid",
            )
        self._last_prepared_dispatch_diagnostic = None
        self._before_send(method=method, path=path)
        if (
            type(path) is not str
            or re.fullmatch(r"/api/v5/[a-z0-9]+(?:[-/][a-z0-9]+)*", path) is None
        ):
            raise OkxPrivateApiError(
                "Private REST path is invalid", code="private_transport_target_rejected"
            )
        # Non-GET requests cannot gain retries by claiming to be reads.
        write = write or method.upper() != "GET"
        query = str(
            httpx.QueryParams(
                {k: v for k, v in (params or {}).items() if v not in (None, "")}
            )
        )
        request_path = path if not query else f"{path}?{query}"
        request_url = self._rest_base_url() + request_path
        order_create = method.upper() == "POST" and path == "/api/v5/trade/order"
        if observe_order and not order_create:
            raise OkxPrivateApiError(
                "Order observation requires the exact order route",
                code="private_order_observation_route_rejected",
            )
        body_text = (
            ""
            if body is None
            else json.dumps(
                body,
                sort_keys=order_create or demo_configuration,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=not (order_create or demo_configuration),
            )
        )
        body_bytes = None if body is None else body_text.encode("utf-8")

        from app.exchange.okx import native_demo_write_v2 as native_write

        native_httpx_client = native_write._native_request_client_v2(self, method, path)
        own_client = self._external_client is None
        client = (
            native_httpx_client
            or self._external_client
            or httpx.AsyncClient(
                base_url=self._rest_base_url(),
                timeout=httpx.Timeout(self._timeout_seconds()),
                trust_env=False,
                follow_redirects=False,
            )
        )
        attempts = 1 if write else self._read_max_retries() + 1
        last_error: Exception | None = None
        selector_response = None
        selector_raw = None
        selector_signature = None
        reconciliation_journal = None
        try:
            if demo_reconciliation:
                reconciliation_journal = reconciliation_read.begin(
                    self,
                    self._active_read_session(),
                    path,
                    params,
                    request_url,
                    transport_client=client,
                )
            for attempt in range(attempts):
                try:
                    # Generate a fresh timestamp and signature for every read retry.
                    # Write operations are single-attempt and therefore cannot be
                    # duplicated by this client after an ambiguous transport error.
                    headers = self._headers(
                        method=method,
                        request_path=request_path,
                        body=body_text,
                    )
                    headers.update(
                        self._request_extra_headers(
                            method=method.upper(),
                            path=path,
                            write=write,
                        )
                    )
                    self._validate_external_client(client)
                    self._before_send(method=method, path=path)
                    # `body_text` is the same immutable string signed above and
                    # passed to HTTPX below. A caller's mutable dict cannot
                    # change the CAA timeout after this final dispatch check.
                    _validate_cancel_all_after_dispatch(
                        method=method,
                        path=path,
                        request_path=request_path,
                        body_text=body_text,
                    )
                    prepared_write_request = None
                    if write:
                        # HTTPX must send this very Request object. Signing and
                        # the byte observation share one immutable body encoding;
                        # a second JSON serialization cannot drift after replay.
                        prepared_write_request = client.build_request(
                            method.upper(),
                            request_url,
                            headers=headers,
                            content=body_bytes,
                        )
                        if native_httpx_client is not None:
                            native_write._bind_prepared_native_request_v2(
                                self, prepared_write_request
                            )
                        if order_create:
                            owner = getattr(self, "_qualification_dispatch_owner", None)
                            slot = getattr(self, "_qualification_dispatch_slot", None)
                            if (
                                type(owner) is DispatchOwnership
                                and type(slot) is DispatchSlot
                            ):
                                try:
                                    environment = (
                                        "demo"
                                        if type(self) is OkxDemoPrivateRestClient
                                        else "live"
                                        if type(self) is OkxLiveExecutionRestClient
                                        else "unknown"
                                    )
                                    self._last_prepared_dispatch_diagnostic = (
                                        owner.observe_prepared_request(
                                            slot,
                                            prepared_write_request,
                                            environment=environment,
                                            selected_request_url=request_url,
                                        )
                                    )
                                except Exception:  # noqa: BLE001 - no private error text
                                    raise OkxPrivateApiError(
                                        "Private write observation is unavailable",
                                        code="private_write_authority_unavailable",
                                    ) from None
                    if bounded_read:
                        prepared_write_request = client.build_request(
                            "GET", request_url, headers=headers, content=None
                        )
                        selector_signature = prepared_write_request.headers.get(
                            "OK-ACCESS-SIGN"
                        )
                        if (
                            type(selector_signature) is not str
                            or not selector_signature
                        ):
                            raise OkxPrivateApiError(
                                "Demo selector signature is unavailable",
                                code="demo_selector_signature_invalid",
                            )
                        if reconciliation_journal is not None:
                            reconciliation_journal.signature = selector_signature
                            reconciliation_read.retain_prepared_request_v1(
                                reconciliation_journal, prepared_write_request
                            )
                    self._final_dispatch_check(method=method, path=path)
                    # Independent of native route selection and legacy route
                    # classifiers: a caller flag or saved receipt grants nothing.
                    require_owned_private_write_authority(
                        method,
                        path,
                        owner=getattr(self, "_qualification_dispatch_owner", None),
                        slot=getattr(self, "_qualification_dispatch_slot", None),
                        client=self,
                    )
                    if prepared_write_request is not None:
                        if observe_order:
                            started_at = observation_utc(self._clock)
                            response = await client.send(
                                prepared_write_request,
                                stream=True,
                                auth=None,
                                follow_redirects=False,
                            )
                            observation = await observe_order_response(
                                prepared_write_request,
                                response,
                                send_invoked_at=started_at,
                                clock=self._clock,
                            )
                            # This is a private, non-authorizing pair.  Only
                            # retained raw response bytes may be classified
                            # against the consumed intent by a future owner.
                            response_digest = (
                                hashlib.sha256(observation.raw_body).hexdigest()
                                if observation.raw_body is not None
                                else None
                            )
                            observed_result = ObservedOrderResult(
                                observation, response_digest
                            )
                            if native_httpx_client is not None:
                                native_write._retain_native_order_observation_v2(
                                    self, observed_result
                                )
                            self._after_response_check(method=method, path=path)
                            return observed_result
                        if demo_configuration:
                            response = await client.send(
                                prepared_write_request,
                                auth=None,
                                follow_redirects=False,
                                stream=True,
                            )
                            selector_response = response
                            native_write._retain_native_leverage_configuration_httpx_response_v1(
                                self, response
                            )
                            # The original Root ResponseStream rejects >64KiB
                            # BEFORE yielding to HTTPX's materialization/JSON.
                            selector_raw = await response.aread()
                            if (
                                type(selector_raw) is not bytes
                                or len(selector_raw) > 65536
                            ):
                                raise OkxPrivateApiError(
                                    "Native configuration response bound",
                                    code="native_configuration_response_bound",
                                )
                            response._content = selector_raw
                            native_write._retain_native_leverage_configuration_httpx_response_v1(
                                self, response
                            )
                        elif bounded_read:
                            response = await client.send(
                                prepared_write_request,
                                auth=None,
                                follow_redirects=False,
                                stream=True,
                            )
                            selector_response = response
                            if reconciliation_journal is not None:
                                reconciliation_read.retain_transport(
                                    reconciliation_journal,
                                    client,
                                    prepared_write_request,
                                    response,
                                )
                            selector_raw = await self._read_demo_selector_bytes_v2(
                                response,
                                _reconciliation_journal=reconciliation_journal,
                                _selector_bound=demo_selector,
                            )
                            if (
                                prepared_write_request.headers.get("OK-ACCESS-SIGN")
                                != selector_signature
                            ):
                                raise OkxPrivateApiError(
                                    "Demo selector request changed",
                                    code="demo_selector_signature_invalid",
                                )
                            # HTTPX's ordinary bounded auth/status handling below
                            # sees exactly the retained uncompressed entity bytes.
                            response._content = selector_raw
                        else:
                            response = await client.send(
                                prepared_write_request,
                                auth=None,
                                follow_redirects=False,
                            )
                    else:
                        response = await client.request(
                            method.upper(),
                            request_url,
                            headers=headers,
                            content=body_text if body is not None else None,
                            auth=None,
                            follow_redirects=False,
                        )
                    self._after_response_check(method=method, path=path)
                    if method.upper() == "GET" and not write:
                        auth_code = _read_only_http_auth_code(response)
                        if auth_code is not None:
                            raise OkxPrivateApiError(
                                "OKX private read authentication rejected",
                                code=auth_code,
                            )
                    response.raise_for_status()
                    payload = (
                        json.loads(
                            selector_raw,
                            object_pairs_hook=_demo_selector_json_pairs_v2,
                            parse_constant=_demo_selector_json_constant_v2,
                            parse_float=_demo_selector_json_float_v2,
                        )
                        if bounded_response
                        else response.json()
                    )
                    response_shape_code = "ambiguous_response" if write else None
                    if not isinstance(payload, dict):
                        raise OkxPrivateApiError(
                            "OKX private API returned a non-object response",
                            code=response_shape_code,
                        )
                    if payload.get("code") != "0":
                        raise _private_response_rejection(
                            payload.get("code"), write=write
                        )
                    data = payload.get("data")
                    if not isinstance(data, list):
                        raise OkxPrivateApiError(
                            "OKX private API returned non-list data",
                            code=response_shape_code,
                        )
                    typed_data = [dict(item) for item in data if isinstance(item, dict)]
                    if len(typed_data) != len(data):
                        raise OkxPrivateApiError(
                            "OKX private API returned invalid data items",
                            code=response_shape_code,
                        )
                    if write:
                        if not typed_data:
                            raise OkxPrivateApiError(
                                "OKX private API returned empty write data",
                                code="ambiguous_response",
                            )
                        for item in typed_data:
                            item_code = item.get("sCode", "0")
                            if item_code != "0":
                                raise _private_response_rejection(item_code, write=True)
                        return _project_successful_write_data(
                            path=path,
                            signed_body_text=body_text,
                            data=typed_data,
                        )
                    if demo_reconciliation:
                        observation = reconciliation_journal.finish()
                        if not demo_selector:
                            return observation
                    if demo_selector:
                        if (
                            response.status_code != 200
                            or type(selector_raw) is not bytes
                            or type(selector_signature) is not str
                        ):
                            raise OkxPrivateApiError(
                                "Demo selector response is invalid",
                                code="demo_selector_response_invalid",
                            )
                        return DemoConfigSelectorObservationV2(
                            tuple(typed_data), selector_raw, selector_signature
                        )
                    return typed_data
                except OkxPrivateApiError:
                    raise
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    last_error = exc
                    if attempt + 1 >= attempts:
                        break
                    await asyncio.sleep(0.25 * (2**attempt))
            raise OkxPrivateApiError(
                f"OKX private API unavailable: {last_error.__class__.__name__ if last_error else 'unknown'}",
                code="transport_error",
            )
        finally:
            try:
                if selector_response is not None:
                    await selector_response.aclose()
            except BaseException:
                if reconciliation_journal is not None:
                    reconciliation_journal.fault("close_failed")
                raise
            finally:
                if (
                    reconciliation_journal is not None
                    and reconciliation_journal.observation is None
                ):
                    reconciliation_journal.fault("read_failed")
                if own_client:
                    try:
                        await client.aclose()
                    except BaseException:
                        if reconciliation_journal is not None:
                            reconciliation_journal.fault("close_failed")
                        raise

    async def _read_demo_selector_bytes_v2(
        self, response, *, _reconciliation_journal=None, _selector_bound=False
    ):
        """Fixed config GET raw body, EOF and size checked before JSON parsing."""
        lease = self._active_read_session()
        if (
            type(self) is not OkxDemoPrivateRestClient
            or type(lease) is not DemoPinnedReadSession
            or type(response) is not httpx.Response
        ):
            raise OkxPrivateApiError(
                "Demo selector stream is unavailable",
                code="demo_selector_stream_invalid",
            )
        lease.verify_current()
        journal = _reconciliation_journal
        if (
            journal is not None
            and type(journal) is not reconciliation_read.ReadJournalV1
        ):
            raise ValueError("native_reconciliation_read_data_invalid")
        maximum = (
            65536
            if journal is None or _selector_bound
            else reconciliation_read.MAX_BODY
        )
        encoding = response.headers.get("content-encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            raise OkxPrivateApiError(
                "Demo selector encoding is unsupported",
                code="demo_selector_stream_invalid",
            )
        declared = response.headers.get("content-length")
        if declared is not None and (
            not declared.isascii()
            or not declared.isdigit()
            or len(declared) > len(str(maximum))
            or int(declared) > maximum
        ):
            raise OkxPrivateApiError(
                "Demo selector body exceeds its bound",
                code="demo_selector_response_bound",
            )
        if journal is not None:
            journal.declared_length = None if declared is None else int(declared)
        chunks, size, chunk_count = [], 0, 0
        iterator = (
            response.aiter_raw() if journal is None else response.stream.__aiter__()
        )
        while True:
            lease.verify_current()
            try:
                chunk = await anext(iterator)
            except StopAsyncIteration:
                if journal is not None:
                    journal.retain_eof()
                lease.verify_current()
                break
            if journal is not None:
                journal.retain_chunk(chunk)
            lease.verify_current()
            self._after_response_check(
                method="GET",
                path="/api/v5/account/config" if journal is None else journal.path,
            )
            chunk_count += 1
            if chunk_count > 65536:
                raise OkxPrivateApiError(
                    "Demo selector chunk count exceeds its bound",
                    code="demo_selector_stream_invalid",
                )
            if journal is None:
                size = _demo_selector_append_chunk_v2(chunks, size, chunk)
            else:
                size = journal.body_bytes
                if size > maximum:
                    journal.fault("response_invalid")
                    raise OkxPrivateApiError(
                        "Demo selector body exceeds its bound",
                        code="demo_selector_response_bound",
                    )
        if size == 0 or (declared is not None and int(declared) != size):
            raise OkxPrivateApiError(
                "Demo selector body is incomplete", code="demo_selector_stream_invalid"
            )
        return b"".join(chunks if journal is None else journal.chunks)

    async def _cursor_chain(
        self,
        path: str,
        *,
        params: dict[str, Any],
        cursor_field: str,
        page_size: int = 100,
        max_pages: int = 16,
    ) -> list[dict[str, Any]]:
        """Read an ordered cursor chain through an explicit empty terminal page."""
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        after = None
        for _ in range(max_pages):
            query = dict(params)
            query["limit"] = str(page_size)
            if after is not None:
                query["after"] = after
            page = await self._request("GET", path, params=query)
            if not page:
                return collected
            if len(page) > page_size:
                raise OkxPrivateApiError(
                    "OKX private cursor page exceeds its declared limit",
                    code="pagination_incomplete",
                )
            identities = [row.get(cursor_field) for row in page]
            if any(
                type(value) is not str
                or re.fullmatch(r"[1-9][0-9]{0,39}", value) is None
                for value in identities
            ):
                raise OkxPrivateApiError(
                    "OKX private cursor identity is missing or invalid",
                    code="pagination_incomplete",
                )
            numeric = [int(value) for value in identities]
            if any(left <= right for left, right in itertools.pairwise(numeric)):
                raise OkxPrivateApiError(
                    "OKX private cursor page is not newest-first",
                    code="pagination_incomplete",
                )
            if after is not None and any(value >= int(after) for value in numeric):
                raise OkxPrivateApiError(
                    "OKX private cursor did not advance",
                    code="pagination_incomplete",
                )
            if any(value in seen for value in identities):
                raise OkxPrivateApiError(
                    "OKX private cursor chain contains duplicate identities",
                    code="pagination_incomplete",
                )
            seen.update(identities)
            collected.extend(page)
            after = identities[-1]
        raise OkxPrivateApiError(
            "OKX private cursor chain did not reach an empty terminal page",
            code="pagination_incomplete",
        )

    async def account_config(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/v5/account/config")

    async def account_config_selector_v2(
        self, *, retain_network=False
    ) -> DemoConfigSelectorObservationV2:
        """One configured Demo GET only; no source/order eligibility is issued."""
        if (
            type(self) is not OkxDemoPrivateRestClient
            or type(retain_network) is not bool
        ):
            raise OkxPrivateApiError(
                "Demo selector purpose is unavailable",
                code="demo_selector_purpose_invalid",
            )
        lease = self._active_read_session()
        if type(lease) is not DemoPinnedReadSession:
            raise OkxPrivateApiError(
                "Demo selector read lease is unavailable",
                code="demo_selector_purpose_invalid",
            )
        lease.verify_current()
        async with asyncio.timeout(5):
            result = await self._request(
                "GET",
                "/api/v5/account/config",
                _demo_selector_max_bytes=65536,
                _demo_reconciliation_max_bytes=reconciliation_read.MAX_BODY
                if retain_network
                else None,
            )
        lease.verify_current()
        if type(result) is not DemoConfigSelectorObservationV2:
            raise OkxPrivateApiError(
                "Demo selector observation is unavailable",
                code="demo_selector_response_invalid",
            )
        return result

    async def native_reconciliation_get_v1(self, path, params):
        """One fresh signed bounded GET page. DATA only, never account completeness.

        Uses the same signing/dispatch/GET-only pinned lease as ordinary reads.
        A new native owner must separately prove every terminal page and identity.
        """
        reconciliation_read.checked_query(path, params)
        async with asyncio.timeout(5):
            result = await self._request(
                "GET",
                path,
                params=params,
                _demo_reconciliation_max_bytes=reconciliation_read.MAX_BODY,
            )
        if type(result) is not reconciliation_read.DemoReconciliationObservationV1:
            raise OkxPrivateApiError(
                "Native reconciliation GET is unavailable",
                code="native_reconciliation_read_invalid",
            )
        return result

    async def balance(self, currency: str | None = None) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/api/v5/account/balance", params={"ccy": currency}
        )

    async def positions(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        params = {} if instrument_id is None else {"instId": instrument_id}
        return await self._request(
            "GET",
            "/api/v5/account/positions",
            params=params,
        )

    async def pending_orders(
        self, instrument_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = {} if instrument_id is None else {"instId": instrument_id}
        return await self._cursor_chain(
            "/api/v5/trade/orders-pending",
            params=params,
            cursor_field="ordId",
            page_size=_PRIVATE_PAGE_SIZE,
            max_pages=_PRIVATE_MAX_PAGES,
        )

    async def order_history(
        self, instrument_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Read the bounded SWAP order-history chain; ``limit`` is per page."""
        if type(limit) is not int or not 1 <= limit <= _PRIVATE_PAGE_SIZE:
            raise OkxPrivateApiError(
                "OKX private order-history page limit is invalid",
                code="pagination_limit_invalid",
            )
        return await self._cursor_chain(
            "/api/v5/trade/orders-history",
            params={"instType": "SWAP", "instId": instrument_id},
            cursor_field="ordId",
            page_size=limit,
            max_pages=_PRIVATE_MAX_PAGES,
        )

    async def pending_algo_orders(
        self, instrument_id: str | None = None
    ) -> list[dict[str, Any]]:
        path = "/api/v5/trade/orders-algo-pending"
        rows = []
        seen: set[str] = set()
        for order_type in _ALGO_ORDER_TYPES:
            current = await self._cursor_chain(
                path,
                params={"ordType": order_type, "instId": instrument_id},
                cursor_field="algoId",
                page_size=_PRIVATE_PAGE_SIZE,
                max_pages=_PRIVATE_MAX_PAGES,
            )
            for row in current:
                if row.get("ordType") != order_type:
                    raise OkxPrivateApiError(
                        "OKX pending algo type differs from its query",
                        code="pagination_incomplete",
                    )
                identity = row["algoId"]
                if identity in seen:
                    raise OkxPrivateApiError(
                        "OKX pending algo identity conflicts across order types",
                        code="pagination_incomplete",
                    )
                seen.add(identity)
            rows.extend(current)
        return rows

    async def order_detail(
        self,
        instrument_id: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/trade/order",
            params={
                "instId": instrument_id,
                "ordId": order_id,
                "clOrdId": client_order_id,
            },
        )

    async def max_order_size(
        self,
        instrument_id: str,
        *,
        margin_mode: str,
        price: str | None = None,
        leverage: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/v5/account/max-size",
            params={
                "instId": instrument_id,
                "tdMode": margin_mode,
                "px": price,
                "leverage": leverage,
            },
        )

    async def order_precheck(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST",
            "/api/v5/trade/order-precheck",
            body=payload,
            write=True,
        )

    async def place_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/trade/order", body=payload, write=True
        )

    async def cancel_order(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/trade/cancel-order", body=payload, write=True
        )

    async def close_position(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/trade/close-position", body=payload, write=True
        )

    async def set_leverage(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST", "/api/v5/account/set-leverage", body=payload, write=True
        )

    async def cancel_all_after(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._request(
            "POST",
            "/api/v5/trade/cancel-all-after",
            body=payload,
            write=True,
        )


class OkxDemoPrivateRestClient(_OkxPrivateRestClientBase):
    """Authenticated OKX Demo REST client with simulation permanently enabled."""

    async def _submit_native_demo_leverage_configuration_v1(
        self, payload, *, native_write_gate
    ):
        from app.exchange.okx import native_demo_write_v2 as native_write

        native_write._require_native_leverage_configuration_client_v1(
            self, native_write_gate, payload
        )
        return await self._request(
            "POST",
            "/api/v5/account/set-leverage",
            body=payload,
            write=True,
            _native_configuration_max_bytes=65536,
        )

    async def place_order_observed(
        self, payload: dict[str, Any]
    ) -> ObservedOrderResult:
        """Future controlled-owner hook; all current order dispatch remains DENY.

        A response is paired with the exact prepared body hash and retained raw
        bytes.  The caller must not treat HTTP 200 as an acknowledgement, and
        must resolve a raised/cancelled call against its durable intent without
        resubmitting.  This method shares the final write guards with place_order.
        """

        result = await self._request(
            "POST",
            "/api/v5/trade/order",
            body=payload,
            write=True,
            observe_order=True,
        )
        if type(result) is not ObservedOrderResult:
            raise OkxPrivateApiError(
                "Order observation result is unavailable",
                code="private_order_observation_unavailable",
            )
        return result

    def _unbound_read_values(
        self,
    ) -> tuple[tuple[str, str, str], str, float, int, str]:
        if not self.settings.okx_demo_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Demo credentials are not configured",
                code="credentials_missing",
            )
        credentials = (
            self.settings.okx_demo_api_key.get_secret_value(),
            self.settings.okx_demo_api_secret.get_secret_value(),
            self.settings.okx_demo_api_passphrase.get_secret_value(),
        )
        origin = _validated_origin(
            self.settings.okx_demo_rest_base_url,
            frozenset(
                {
                    "openapi.okx.com",
                    "www.okx.com",
                    "us.okx.com",
                    "eea.okx.com",
                    "tr.okx.com",
                }
            ),
        )
        return (
            credentials,
            origin,
            self.settings.okx_demo_timeout_seconds,
            self.settings.okx_demo_read_max_retries,
            f"CTCC-V2/{self.settings.app_version}",
        )

    @contextmanager
    def pinned_read_session(self) -> Iterator[DemoPinnedReadSession]:
        """Pin all Demo GET signatures and transport policy across awaits/tasks.

        ContextVar propagation gives every asyncio.gather child the same frozen
        credential set. A closed session cannot authorize a detached child task.
        """
        if type(self) is not OkxDemoPrivateRestClient or _DEMO_READ_SESSION.get():
            raise OkxPrivateApiError(
                "Demo read session cannot be nested or substituted",
                code="demo_read_session_invalid",
            )
        if self.settings.environment != "test" and (
            self._external_client is not None or self._clock_injected
        ):
            raise OkxPrivateApiError(
                "Demo read transport is not runtime-owned",
                code="demo_read_transport_invalid",
            )
        credentials, origin, timeout, retries, user_agent = self._unbound_read_values()
        lease = _DemoReadLease()
        session = DemoPinnedReadSession(
            _owner=self,
            _settings=self.settings,
            _environment=self.settings.environment,
            _credentials=credentials,
            _origin=origin,
            _timeout_seconds=timeout,
            _read_max_retries=retries,
            _user_agent=user_agent,
            _lease=lease,
            binding_id=secrets.token_hex(32),
        )
        token = _DEMO_READ_SESSION.set(session)
        try:
            session.verify_current()
            yield session
        finally:
            lease.active = False
            _DEMO_READ_SESSION.reset(token)

    def _active_read_session(self) -> DemoPinnedReadSession | None:
        session = _DEMO_READ_SESSION.get()
        if session is None:
            return None
        if session._owner is not self:
            from app.exchange.okx import native_demo_write_v2 as native_write

            if native_write._native_foreign_read_context_v2(self, session):
                return None
            raise OkxPrivateApiError(
                "Demo read session belongs to another client",
                code="demo_read_session_invalid",
            )
        session.verify_current()
        return session

    @property
    def active_read_session_binding_id(self) -> str | None:
        session = self._active_read_session()
        return None if session is None else session.binding_id

    def _before_send(self, *, method: str, path: str) -> None:
        session = self._active_read_session()
        if session is not None and (type(method) is not str or method.upper() != "GET"):
            raise OkxPrivateApiError(
                "Pinned Demo session allows GET only",
                code="demo_read_session_get_only",
            )
        from app.exchange.okx import native_demo_write_v2 as native_write

        if native_write._native_route_v2(self, method, path):
            return
        enforce_demo_submission_boundary(method, path)

    def _final_dispatch_check(self, *, method: str, path: str) -> None:
        self._active_read_session()
        from app.exchange.okx import native_demo_write_v2 as native_write

        if native_write._native_route_v2(self, method, path):
            return
        enforce_demo_final_dispatch(method, path)

    def _after_response_check(self, *, method: str, path: str) -> None:
        self._active_read_session()

    def _credentials(self) -> tuple[str, str, str]:
        session = self._active_read_session()
        if session is not None:
            return session._credentials
        if not self.settings.okx_demo_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Demo credentials are not configured",
                code="credentials_missing",
            )
        return (
            self.settings.okx_demo_api_key.get_secret_value(),
            self.settings.okx_demo_api_secret.get_secret_value(),
            self.settings.okx_demo_api_passphrase.get_secret_value(),
        )

    def _rest_base_url(self) -> str:
        session = self._active_read_session()
        if session is not None:
            return session._origin
        return _validated_origin(
            self.settings.okx_demo_rest_base_url,
            frozenset(
                {
                    "openapi.okx.com",
                    "www.okx.com",
                    "us.okx.com",
                    "eea.okx.com",
                    "tr.okx.com",
                }
            ),
        )

    def _timeout_seconds(self) -> float:
        session = self._active_read_session()
        if session is not None:
            return session._timeout_seconds
        return self.settings.okx_demo_timeout_seconds

    def _read_max_retries(self) -> int:
        session = self._active_read_session()
        if session is not None:
            return session._read_max_retries
        return self.settings.okx_demo_read_max_retries

    def _user_agent(self) -> str:
        session = self._active_read_session()
        if session is not None:
            return session._user_agent
        return super()._user_agent()

    def _extra_headers(self) -> dict[str, str]:
        return {"x-simulated-trading": "1"}


class OkxLivePrivateRestClient(_OkxPrivateRestClientBase):
    """Authenticated OKX Production REST client with writes hard-blocked."""

    def _before_send(self, *, method: str, path: str) -> None:
        # Keep the read-only invariant in the shared transport's final check.
        # Explicit base-method dispatch must not bypass this client's _request.
        if type(method) is not str or method.upper() != "GET":
            raise OkxPrivateApiError(
                "OKX Live write operations are disabled",
                code="live_writes_disabled",
            )

    def _credentials(self) -> tuple[str, str, str]:
        if not self.settings.okx_live_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Live credentials are not configured",
                code="credentials_missing",
            )
        return (
            self.settings.okx_live_api_key.get_secret_value(),
            self.settings.okx_live_api_secret.get_secret_value(),
            self.settings.okx_live_api_passphrase.get_secret_value(),
        )

    def _rest_base_url(self) -> str:
        return _validated_origin(
            self.settings.okx_live_rest_base_url,
            frozenset({"openapi.okx.com", "eea.okx.com"}),
        )

    def _timeout_seconds(self) -> float:
        return self.settings.okx_live_timeout_seconds

    def _read_max_retries(self) -> int:
        return self.settings.okx_live_read_max_retries

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        if write or method.upper() != "GET":
            raise OkxPrivateApiError(
                "OKX Live write operations are disabled",
                code="live_writes_disabled",
            )
        return await super()._request(
            method,
            path,
            params=params,
            body=body,
            write=False,
        )


class OkxLiveExecutionRestClient(_OkxPrivateRestClientBase):
    """Production write transport reachable only through explicit Live gates.

    This class never sets the simulated-trading header and never retries a
    write. The separate read-only Live client remains permanently unable to
    send non-GET requests.
    """

    def _ensure_write_configuration(self) -> None:
        if not (
            self.settings.environment == "production"
            and self.settings.trading_mode == "live"
            and self.settings.okx_live_enabled
            and self.settings.live_trading
            and self.settings.okx_live_allow_order_writes
            and self.settings.web_concurrency == 1
        ):
            raise OkxPrivateApiError(
                "OKX Live execution transport is not enabled",
                code="live_execution_not_enabled",
            )

    def _before_send(self, *, method: str, path: str) -> None:
        if method.upper() != "GET":
            self._ensure_write_configuration()
        enforce_live_submission_boundary(method, path)

    def _final_dispatch_check(self, *, method: str, path: str) -> None:
        self._before_send(method=method, path=path)
        enforce_live_final_dispatch(method, path)

    def _credentials(self) -> tuple[str, str, str]:
        if not self.settings.okx_live_credentials_configured:
            raise OkxPrivateApiError(
                "OKX Live credentials are not configured",
                code="credentials_missing",
            )
        return (
            self.settings.okx_live_api_key.get_secret_value(),
            self.settings.okx_live_api_secret.get_secret_value(),
            self.settings.okx_live_api_passphrase.get_secret_value(),
        )

    def _rest_base_url(self) -> str:
        return _validated_origin(
            self.settings.okx_live_rest_base_url,
            frozenset({"openapi.okx.com", "eea.okx.com"}),
        )

    def _timeout_seconds(self) -> float:
        return self.settings.okx_live_timeout_seconds

    def _read_max_retries(self) -> int:
        return self.settings.okx_live_read_max_retries

    def _request_extra_headers(
        self, *, method: str, path: str, write: bool
    ) -> dict[str, str]:
        if write and method == "POST" and path == "/api/v5/trade/order":
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            expiry = int(now.astimezone(UTC).timestamp() * 1000)
            expiry += self.settings.okx_live_order_expiry_milliseconds
            return {"expTime": str(expiry)}
        return {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> list[dict[str, Any]]:
        if write or method.upper() != "GET":
            self._ensure_write_configuration()
            return await super()._request(
                method,
                path,
                params=params,
                body=body,
                write=True,
            )
        return await super()._request(
            method,
            path,
            params=params,
            body=body,
            write=False,
        )
